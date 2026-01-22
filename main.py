from flask import Flask, request, make_response
import requests
import os
import time
import threading

app = Flask(__name__)

# ================= ENV =================
BOT_TOKEN = os.environ["BOT_TOKEN"]
ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_PROJECT_ID = os.environ["ASANA_PROJECT_ID"]
ASANA_ASSIGNEE_ID = os.environ["ASANA_ASSIGNEE_ID"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}

REQUIRED_PHOTOS = 3

# ================= STATE =================
user_states = {}        # chat_id -> state
user_data = {}          # chat_id -> data
sent_notifications = set()

# ================= TEXTS =================
TEXTS = {
    "ru": {
        "choose_lang": "Выберите язык / Tilni tanlang",
        "start_info": (
            "ℹ️ Фото-контроль брендированного автомобиля\n\n"
            "Порядок проверки:\n"
            "1️⃣ ФИО\n"
            "2️⃣ Табельный номер\n"
            "3️⃣ 3 фотографии автомобиля\n\n"
            "Результат придёт в этот чат."
        ),
        "fio": "✍️ Шаг 1 из 3\nВведите ФИО полностью",
        "tab": "🔢 Шаг 2 из 3\nВведите табельный номер",
        "photo": (
            "📸 Шаг 3 из 3\n\n"
            "Отправьте 3 фото автомобиля:\n"
            "• авто целиком\n"
            "• номер виден\n"
            "• брендировка видна"
        ),
        "photo_left": "📸 Фото принято. Осталось: {n}",
        "photo_done": "📸 Все фото получены.",
        "submitted": (
            "⏳ Заявка принята\n"
            "Материалы переданы на проверку."
        ),
        "approved": (
            "✅ Фото-контроль пройден\n\n"
            "Автомобиль соответствует требованиям."
        ),
        "rejected": (
            "❌ Фото-контроль не пройден\n\n"
            "Причина:\n{reason}"
        ),
        "need_photos": "Необходимо отправить ровно 3 фото.",
        "cancel": "❌ Операция отменена.",
        "buttons": {
            "start": "▶️ Начать",
            "cancel": "❌ Отменить",
            "finish": "✅ Завершить"
        }
    },
    "uz": {
        "choose_lang": "Tilni tanlang / Выберите язык",
        "start_info": (
            "ℹ️ Brendlangan avtomobil uchun foto-nazorat\n\n"
            "Tekshiruv tartibi:\n"
            "1️⃣ F.I.Sh.\n"
            "2️⃣ Tabel raqami\n"
            "3️⃣ 3 ta fotosurat\n\n"
            "Natija shu chatga yuboriladi."
        ),
        "fio": "✍️ 1-bosqich\nF.I.Sh. ni kiriting",
        "tab": "🔢 2-bosqich\nTabel raqamini kiriting",
        "photo": (
            "📸 3-bosqich\n\n"
            "Avtomobilning 3 ta fotosuratini yuboring"
        ),
        "photo_left": "📸 Qabul qilindi. Qolgan: {n}",
        "photo_done": "📸 Barcha fotosuratlar qabul qilindi.",
        "submitted": "⏳ Ariza qabul qilindi.",
        "approved": "✅ Foto-nazoratdan o‘tildi.",
        "rejected": "❌ O‘tilmadi.\nSabab:\n{reason}",
        "need_photos": "3 ta fotosurat kerak.",
        "cancel": "❌ Bekor qilindi.",
        "buttons": {
            "start": "▶️ Boshlash",
            "cancel": "❌ Bekor qilish",
            "finish": "✅ Yakunlash"
        }
    }
}

# ================= HELPERS =================
def kb(buttons):
    return {"keyboard": [[{"text": b}] for b in buttons], "resize_keyboard": True}

def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)

def reset_to_start(chat_id, lang):
    user_states[chat_id] = "READY"
    user_data[chat_id] = {"lang": lang, "photos": []}
    send(chat_id, TEXTS[lang]["start_info"], kb([TEXTS[lang]["buttons"]["start"]]))

def download_file(file_id):
    info = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    path = info["result"]["file_path"]
    return requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}").content

# ================= ASANA =================
def create_asana_task(fio, tab, tg_id, photos, lang):
    notes = f"ФИО:\n{fio}\n\nLANG:{lang}"

    # получаем кастомные поля проекта
    fields = requests.get(
        f"https://app.asana.com/api/1.0/projects/{ASANA_PROJECT_ID}/custom_field_settings",
        headers=ASANA_HEADERS,
        timeout=10
    ).json()["data"]

    custom_fields = {}
    for item in fields:
        f = item["custom_field"]
        if f["name"] == "Telegram ID":
            custom_fields[f["gid"]] = str(tg_id)
        if f["name"] == "Табель №":
            custom_fields[f["gid"]] = tab

    task = requests.post(
        "https://app.asana.com/api/1.0/tasks",
        headers={**ASANA_HEADERS, "Content-Type": "application/json"},
        json={"data": {
            "name": "Заявка на фото-контроль",
            "notes": notes,
            "projects": [ASANA_PROJECT_ID],
            "assignee": ASANA_ASSIGNEE_ID,
            "resource_subtype": "approval",
            "approval_status": "pending",
            "custom_fields": custom_fields
        }},
        timeout=10
    ).json()["data"]

    for p in photos:
        requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task['gid']}/attachments",
            headers=ASANA_HEADERS,
            files={"file": p},
            timeout=20
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

# ================= TELEGRAM =================
@app.route("/webhook", methods=["POST"])
def telegram():
    msg = (request.json or {}).get("message")
    if not msg:
        return "ok"

    cid = msg["chat"]["id"]
    txt = msg.get("text", "")
    photos = msg.get("photo")
    state = user_states.get(cid)

    if txt == "/start" or cid not in user_data:
        user_states[cid] = "LANG"
        user_data[cid] = {}
        send(cid, TEXTS["ru"]["choose_lang"], kb(["Русский 🇷🇺", "O‘zbek 🇺🇿"]))
        return "ok"

    if state == "LANG":
        lang = "uz" if "O‘zbek" in txt else "ru"
        reset_to_start(cid, lang)
        return "ok"

    lang = user_data[cid]["lang"]
    btn = TEXTS[lang]["buttons"]

    if state == "READY" and txt == btn["start"]:
        user_states[cid] = "WAIT_FIO"
        send(cid, TEXTS[lang]["fio"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_FIO":
        if txt == btn["cancel"]:
            reset_to_start(cid, lang)
            return "ok"
        user_data[cid]["fio"] = txt
        user_states[cid] = "WAIT_TAB"
        send(cid, TEXTS[lang]["tab"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_TAB":
        if txt == btn["cancel"]:
            reset_to_start(cid, lang)
            return "ok"
        user_data[cid]["tab"] = txt
        user_states[cid] = "WAIT_PHOTO"
        user_data[cid]["photos"] = []
        send(cid, TEXTS[lang]["photo"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_PHOTO":
        if txt == btn["cancel"]:
            reset_to_start(cid, lang)
            return "ok"

        if photos:
            if len(user_data[cid]["photos"]) < REQUIRED_PHOTOS:
                user_data[cid]["photos"].append(download_file(photos[-1]["file_id"]))
                left = REQUIRED_PHOTOS - len(user_data[cid]["photos"])
                if left > 0:
                    send(cid, TEXTS[lang]["photo_left"].format(n=left))
                else:
                    send(cid, TEXTS[lang]["photo_done"], kb([btn["finish"]]))
            return "ok"

        if txt == btn["finish"]:
            if len(user_data[cid]["photos"]) != REQUIRED_PHOTOS:
                send(cid, TEXTS[lang]["need_photos"])
                return "ok"
            create_asana_task(
                user_data[cid]["fio"],
                user_data[cid]["tab"],
                cid,
                user_data[cid]["photos"],
                lang
            )
            send(cid, TEXTS[lang]["submitted"])
            reset_to_start(cid, lang)
            return "ok"

    return "ok"

# ================= ASANA WEBHOOK =================
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
        text = TEXTS[lang]["approved"] if status == "approved" else TEXTS[lang]["rejected"].format(reason=reason)

        task = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=ASANA_HEADERS,
            params={"opt_fields": "custom_fields.name,custom_fields.display_value"}
        ).json()["data"]

        for f in task["custom_fields"]:
            if f["name"] == "Telegram ID":
                send(int(f["display_value"]), text)
        return

@app.route("/")
def root():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))









