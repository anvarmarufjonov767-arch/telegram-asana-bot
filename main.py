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
            "Для прохождения проверки выполните следующие шаги:\n"
            "1. Укажите ФИО\n"
            "2. Укажите табельный номер\n"
            "3. Отправьте 3 фотографии автомобиля\n\n"
            "После проверки вы получите уведомление о результате."
        ),
        "fio": "✍️ Шаг 1 из 3\n\nВведите ФИО полностью",
        "tab": "🔢 Шаг 2 из 3\n\nВведите табельный номер",
        "photo": (
            "📸 Шаг 3 из 3\n\n"
            "Отправьте 3 фотографии автомобиля.\n\n"
            "Требования:\n"
            "• автомобиль целиком\n"
            "• отчётливо виден государственный номер\n"
            "• отчётливо видна брендировка"
        ),
        "photo_left": "📸 Фотография получена.\n\nНеобходимо отправить ещё {n} фотографию(и).",
        "photo_done": "📸 Все необходимые фотографии получены.",
        "submitted": (
            "⏳ Заявка на фото-контроль принята\n\n"
            "Материалы переданы на проверку.\n"
            "Результат будет направлен в данном чате."
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
        "cancel": "Операция отменена.",
        "need_photos": "Для завершения необходимо отправить ровно 3 фотографии."
    },
    "uz": {
        "choose_lang": "Tilni tanlang / Выберите язык",
        "start": (
            "ℹ️ Brendlangan avtomobil uchun foto-nazorat\n\n"
            "Tekshiruvdan o‘tish uchun:\n"
            "1. F.I.Sh. ni kiriting\n"
            "2. Tabel raqamini kiriting\n"
            "3. Avtomobilning 3 ta fotosuratini yuboring\n\n"
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
        "photo_left": "📸 Foto qabul qilindi.\n\nYana {n} ta fotosurat yuboring.",
        "photo_done": "📸 Barcha fotosuratlar qabul qilindi.",
        "submitted": (
            "⏳ Foto-nazorat uchun ariza qabul qilindi\n\n"
            "Materiallar tekshiruvga yuborildi.\n"
            "Natija ushbu chat orqali yuboriladi."
        ),
        "approved": (
            "✅ Foto-nazoratdan muvaffaqiyatli o‘tildi\n\n"
            "Avtomobil belgilangan talablarga mos keladi.\n"
            "Rahmat."
        ),
        "rejected": (
            "❌ Foto-nazoratdan o‘tilmadi\n\n"
            "Sabab:\n{reason}\n\n"
            "Iltimos, kamchiliklarni bartaraf etib,\n"
            "fotosuratlarni qayta yuboring."
        ),
        "cancel": "Amal bekor qilindi.",
        "need_photos": "Yakunlash uchun 3 ta fotosurat yuborilishi kerak."
    }
}

# ========= HELPERS =========
def kb(rows):
    return {"keyboard": [[{"text": b} for b in row] for row in rows], "resize_keyboard": True}


def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)


def t(chat_id, key, **kwargs):
    lang = user_data.get(chat_id, {}).get("lang", "ru")
    return TEXTS[lang][key].format(**kwargs)


def download_file(file_id):
    info = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    path = info["result"]["file_path"]
    return requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}").content


# ========= ASANA =========
def create_asana_task(fio, tab, telegram_id, photos):
    fields = requests.get(
        f"https://app.asana.com/api/1.0/projects/{ASANA_PROJECT_ID}/custom_field_settings",
        headers=ASANA_HEADERS
    ).json()["data"]

    custom_fields = {}
    for item in fields:
        f = item["custom_field"]
        if f["name"] == "Табель №":
            custom_fields[f["gid"]] = tab
        if f["name"] == "Telegram ID":
            custom_fields[f["gid"]] = str(telegram_id)

    task = requests.post(
        "https://app.asana.com/api/1.0/tasks",
        headers={**ASANA_HEADERS, "Content-Type": "application/json"},
        json={"data": {
            "name": "Заявка на фото-контроль",
            "notes": f"ФИО:\n{fio}",
            "projects": [ASANA_PROJECT_ID],
            "assignee": ASANA_ASSIGNEE_ID,
            "resource_subtype": "approval",
            "approval_status": "pending",
            "custom_fields": custom_fields
        }}
    ).json()["data"]

    for p in photos:
        requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task['gid']}/attachments",
            headers=ASANA_HEADERS,
            files={"file": p}
        )


def get_last_comment(task_gid):
    r = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
        headers=ASANA_HEADERS
    ).json()
    for s in reversed(r.get("data", [])):
        if s.get("type") == "comment":
            return s.get("text")
    return "-"


# ========= TELEGRAM =========
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.json or {}
    msg = data.get("message")
    if not msg:
        return "ok"

    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    photos = msg.get("photo")

    state = user_states.get(chat_id)

    if text == "/start":
        user_states[chat_id] = "LANG"
        user_data[chat_id] = {"photos": []}
        send_message(chat_id, TEXTS["ru"]["choose_lang"], kb([["Русский 🇷🇺"], ["O‘zbek 🇺🇿"]]))
        return "ok"

    if state == "LANG":
        user_data[chat_id]["lang"] = "uz" if "O‘zbek" in text else "ru"
        user_states[chat_id] = "FIO"
        send_message(chat_id, t(chat_id, "start"), kb([["Начать"]]))
        return "ok"

    if text == "Начать" and state == "FIO":
        send_message(chat_id, t(chat_id, "fio"), kb([["Отменить"]]))
        user_states[chat_id] = "WAIT_FIO"
        return "ok"

    if state == "WAIT_FIO":
        if text == "Отменить":
            send_message(chat_id, t(chat_id, "cancel"))
            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)
            return "ok"
        user_data[chat_id]["fio"] = text
        user_states[chat_id] = "WAIT_TAB"
        send_message(chat_id, t(chat_id, "tab"), kb([["Отменить"]]))
        return "ok"

    if state == "WAIT_TAB":
        if text == "Отменить":
            send_message(chat_id, t(chat_id, "cancel"))
            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)
            return "ok"
        user_data[chat_id]["tab"] = text
        user_states[chat_id] = "WAIT_PHOTO"
        send_message(chat_id, t(chat_id, "photo"), kb([["Отменить"]]))
        return "ok"

    if state == "WAIT_PHOTO":
        if text == "Отменить":
            send_message(chat_id, t(chat_id, "cancel"))
            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)
            return "ok"

        if photos:
            if len(user_data[chat_id]["photos"]) >= REQUIRED_PHOTOS:
                return "ok"
            user_data[chat_id]["photos"].append(download_file(photos[-1]["file_id"]))
            left = REQUIRED_PHOTOS - len(user_data[chat_id]["photos"])
            if left > 0:
                send_message(chat_id, t(chat_id, "photo_left", n=left))
            else:
                send_message(chat_id, t(chat_id, "photo_done"), kb([["Завершить"]]))
            return "ok"

        if text == "Завершить":
            if len(user_data[chat_id]["photos"]) != REQUIRED_PHOTOS:
                send_message(chat_id, t(chat_id, "need_photos"))
                return "ok"
            d = user_data[chat_id]
            create_asana_task(d["fio"], d["tab"], chat_id, d["photos"])
            send_message(chat_id, t(chat_id, "submitted"))
            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)
            return "ok"

    return "ok"


# ========= ASANA WEBHOOK =========
@app.route("/asana", methods=["GET", "POST"])
def asana_webhook():
    secret = request.headers.get("X-Hook-Secret")
    if secret:
        r = make_response("")
        r.headers["X-Hook-Secret"] = secret
        return r

    if request.method == "GET":
        return "ok"

    data = request.json or {}
    for e in data.get("events", []):
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
            params={"opt_fields": "approval_status,custom_fields.name,custom_fields.display_value"}
        )
        if r.status_code != 200:
            continue

        task = r.json()["data"]
        status = task.get("approval_status")
        if status == "pending":
            continue

        key = f"{task_gid}:{status}"
        if key in sent_notifications:
            return
        sent_notifications.add(key)

        tg = None
        for f in task.get("custom_fields", []):
            if f["name"] == "Telegram ID":
                tg = int(f["display_value"])

        if not tg:
            return

        if status == "approved":
            send_message(tg, TEXTS["ru"]["approved"])
        else:
            reason = get_last_comment(task_gid)
            send_message(tg, TEXTS["ru"]["rejected"].format(reason=reason))
        return


@app.route("/")
def index():
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))









