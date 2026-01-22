from flask import Flask, request, make_response
import requests
import os
import time
import threading

app = Flask(__name__)

# ========= ENV =========
BOT_TOKEN = os.environ["BOT_TOKEN"]
ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_PROJECT_ID = os.environ["ASANA_PROJECT_ID"]
ASANA_ASSIGNEE_ID = os.environ["ASANA_ASSIGNEE_ID"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}

REQUIRED_PHOTOS = 3

# ========= STATE =========
user_states = {}
user_data = {}
sent_notifications = set()

# ========= TEXTS =========
TEXTS = {
    "ru": {
        "choose_lang": "Выберите язык / Tilni tanlang",
        "start": (
            "ℹ️ Фото-контроль брендированного автомобиля\n\n"
            "Для прохождения проверки:\n"
            "1️⃣ Укажите ФИО\n"
            "2️⃣ Укажите табельный номер\n"
            "3️⃣ Отправьте 3 фотографии автомобиля\n\n"
            "После проверки вы получите уведомление."
        ),
        "fio": "✍️ Шаг 1 из 3\n\nВведите ФИО полностью",
        "tab": "🔢 Шаг 2 из 3\n\nВведите табельный номер",
        "photo": (
            "📸 Шаг 3 из 3\n\n"
            "Отправьте 3 фотографии автомобиля.\n\n"
            "Требования:\n"
            "• автомобиль целиком\n"
            "• отчётливо виден номер\n"
            "• отчётливо видна брендировка"
        ),
        "photo_left": "📸 Фото получено.\n\nОсталось отправить: {n}",
        "photo_done": "📸 Все фотографии получены.",
        "submitted": (
            "⏳ Заявка принята\n\n"
            "Материалы переданы на проверку.\n"
            "Результат будет направлен в этом чате."
        ),
        "approved": (
            "✅ Фото-контроль пройден\n\n"
            "Ваш автомобиль соответствует установленным требованиям.\n"
            "Спасибо."
        ),
        "rejected": (
            "❌ Фото-контроль не пройден\n\n"
            "Причина:\n{reason}\n\n"
            "Пожалуйста, устраните замечания и отправьте фотографии повторно."
        ),
        "need_photos": "Для завершения необходимо отправить ровно 3 фотографии.",
        "cancel": "❌ Операция отменена.",
        "buttons": {
            "start": "▶️ Начать",
            "finish": "✅ Завершить",
            "cancel": "❌ Отменить"
        }
    },
    "uz": {
        "choose_lang": "Tilni tanlang / Выберите язык",
        "start": (
            "ℹ️ Brendlangan avtomobil uchun foto-nazorat\n\n"
            "Tekshiruvdan o‘tish uchun:\n"
            "1️⃣ F.I.Sh. ni kiriting\n"
            "2️⃣ Tabel raqamini kiriting\n"
            "3️⃣ Avtomobilning 3 ta fotosuratini yuboring\n\n"
            "Natija ushbu chat orqali yuboriladi."
        ),
        "fio": "✍️ 1-bosqich (3 dan)\n\nF.I.Sh. ni kiriting",
        "tab": "🔢 2-bosqich (3 dan)\n\nTabel raqamini kiriting",
        "photo": (
            "📸 3-bosqich (3 dan)\n\n"
            "3 ta avtomobil fotosuratini yuboring.\n\n"
            "Talablar:\n"
            "• avtomobil to‘liq ko‘rinishi\n"
            "• davlat raqami aniq\n"
            "• brendlash aniq"
        ),
        "photo_left": "📸 Foto qabul qilindi.\n\nQolgan: {n}",
        "photo_done": "📸 Barcha fotosuratlar qabul qilindi.",
        "submitted": (
            "⏳ Ariza qabul qilindi\n\n"
            "Materiallar tekshiruvga yuborildi.\n"
            "Natija shu yerda yuboriladi."
        ),
        "approved": (
            "✅ Foto-nazoratdan muvaffaqiyatli o‘tildi\n\n"
            "Avtomobil belgilangan talablarga mos keladi.\n"
            "Rahmat."
        ),
        "rejected": (
            "❌ Foto-nazoratdan o‘tilmadi\n\n"
            "Sabab:\n{reason}\n\n"
            "Iltimos, kamchiliklarni bartaraf etib, fotosuratlarni qayta yuboring."
        ),
        "need_photos": "Yakunlash uchun 3 ta fotosurat yuborilishi kerak.",
        "cancel": "❌ Amal bekor qilindi.",
        "buttons": {
            "start": "▶️ Boshlash",
            "finish": "✅ Yakunlash",
            "cancel": "❌ Bekor qilish"
        }
    }
}

# ========= HELPERS =========
def kb(buttons):
    return {"keyboard": [[{"text": b}] for b in buttons], "resize_keyboard": True}


def remove_kb():
    return {"remove_keyboard": True}


def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)


def t(chat_id, key, **kw):
    lang = user_data.get(chat_id, {}).get("lang", "ru")
    return TEXTS[lang][key].format(**kw)


# ========= FILE =========
def download_file(file_id):
    info = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    path = info["result"]["file_path"]
    return requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}").content


# ========= ASANA =========
def create_asana_task(fio, tab, tg_id, photos, lang):
    notes = f"ФИО:\n{fio}\n\nLANG:{lang}"

    task = requests.post(
        "https://app.asana.com/api/1.0/tasks",
        headers={**ASANA_HEADERS, "Content-Type": "application/json"},
        json={"data": {
            "name": "Заявка на фото-контроль",
            "notes": notes,
            "projects": [ASANA_PROJECT_ID],
            "assignee": ASANA_ASSIGNEE_ID,
            "resource_subtype": "approval",
            "approval_status": "pending"
        }}
    ).json()["data"]

    for p in photos:
        requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task['gid']}/attachments",
            headers=ASANA_HEADERS,
            files={"file": p}
        )


def get_task_lang_and_comment(task_gid):
    task = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers=ASANA_HEADERS,
        params={"opt_fields": "notes"}
    ).json()["data"]

    lang = "uz" if "LANG:uz" in task.get("notes", "") else "ru"

    stories = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
        headers=ASANA_HEADERS
    ).json()["data"]

    for s in reversed(stories):
        if s.get("type") == "comment":
            return lang, s.get("text")

    return lang, "-"


# ========= TELEGRAM =========
@app.route("/webhook", methods=["POST"])
def telegram():
    msg = (request.json or {}).get("message")
    if not msg:
        return "ok"

    cid = msg["chat"]["id"]
    txt = msg.get("text")
    photos = msg.get("photo")
    state = user_states.get(cid)

    if txt == "/start":
        user_states[cid] = "LANG"
        user_data[cid] = {"photos": []}
        send(cid, TEXTS["ru"]["choose_lang"], kb(["Русский 🇷🇺", "O‘zbek 🇺🇿"]))
        return "ok"

    if state == "LANG":
        user_data[cid]["lang"] = "uz" if "O‘zbek" in txt else "ru"
        user_states[cid] = "FIO"
        send(cid, t(cid, "start"), kb([TEXTS[user_data[cid]["lang"]]["buttons"]["start"]]))
        return "ok"

    if state == "FIO" and txt:
        user_data[cid]["fio"] = txt
        user_states[cid] = "TAB"
        send(cid, t(cid, "tab"), kb([TEXTS[user_data[cid]["lang"]]["buttons"]["cancel"]]))
        return "ok"

    if state == "TAB" and txt:
        if txt.startswith("❌"):
            send(cid, t(cid, "cancel"), remove_kb())
            user_states.pop(cid, None)
            user_data.pop(cid, None)
            return "ok"
        user_data[cid]["tab"] = txt
        user_states[cid] = "PHOTO"
        send(cid, t(cid, "photo"), kb([TEXTS[user_data[cid]["lang"]]["buttons"]["cancel"]]))
        return "ok"

    if state == "PHOTO":
        if photos:
            if len(user_data[cid]["photos"]) < REQUIRED_PHOTOS:
                user_data[cid]["photos"].append(download_file(photos[-1]["file_id"]))
                left = REQUIRED_PHOTOS - len(user_data[cid]["photos"])
                if left > 0:
                    send(cid, t(cid, "photo_left", n=left))
                else:
                    send(
                        cid,
                        t(cid, "photo_done"),
                        kb([TEXTS[user_data[cid]["lang"]]["buttons"]["finish"]])
                    )
            return "ok"

        if txt.startswith("✅"):
            if len(user_data[cid]["photos"]) != REQUIRED_PHOTOS:
                send(cid, t(cid, "need_photos"))
                return "ok"

            d = user_data[cid]
            create_asana_task(d["fio"], d["tab"], cid, d["photos"], d["lang"])
            send(cid, t(cid, "submitted"), remove_kb())
            user_states.pop(cid, None)
            user_data.pop(cid, None)
            return "ok"

        if txt.startswith("❌"):
            send(cid, t(cid, "cancel"), remove_kb())
            user_states.pop(cid, None)
            user_data.pop(cid, None)
            return "ok"

    return "ok"


# ========= ASANA WEBHOOK =========
@app.route("/asana", methods=["GET", "POST"])
def asana():
    secret = request.headers.get("X-Hook-Secret")
    if secret:
        r = make_response("")
        r.headers["X-Hook-Secret"] = secret
        return r

    if request.method == "GET":
        return "ok"

    for e in (request.json or {}).get("events", []):
        gid = e.get("resource", {}).get("gid")
        if gid:
            threading.Thread(target=process_task, args=(gid,), daemon=True).start()
    return "ok"


def process_task(task_gid):
    for _ in range(6):
        time.sleep(2)
        r = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=ASANA_HEADERS,
            params={"opt_fields": "approval_status"}
        )
        if r.status_code != 200:
            continue

        status = r.json()["data"]["approval_status"]
        if status == "pending":
            continue

        key = f"{task_gid}:{status}"
        if key in sent_notifications:
            return
        sent_notifications.add(key)

        lang, reason = get_task_lang_and_comment(task_gid)
        msg = TEXTS[lang]["approved"] if status == "approved" else TEXTS[lang]["rejected"].format(reason=reason)

        # telegram id берём из custom field
        task = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=ASANA_HEADERS,
            params={"opt_fields": "custom_fields.name,custom_fields.display_value"}
        ).json()["data"]

        for f in task["custom_fields"]:
            if f["name"] == "Telegram ID":
                send(int(f["display_value"]), msg)
        return


@app.route("/")
def root():
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))









