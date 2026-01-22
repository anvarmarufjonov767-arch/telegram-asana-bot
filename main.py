from flask import Flask, request, make_response
import requests
import os
import time
import threading

app = Flask(__name__)

# ========== ENV ==========
BOT_TOKEN = os.environ["BOT_TOKEN"]
ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_PROJECT_ID = os.environ["ASANA_PROJECT_ID"]
ASANA_ASSIGNEE_ID = os.environ["ASANA_ASSIGNEE_ID"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}

REQUIRED_PHOTOS = 3

# ========== STATE ==========
user_states = {}
user_data = {}
sent_notifications = set()

# ========== TEXTS ==========
TEXTS = {
    "ru": {
        "choose_lang": "Выберите язык / Tilni tanlang",
        "start_info": (
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
            "Отправьте 3 фотографии автомобиля."
        ),
        "photo_left": "📸 Фото получено. Осталось: {n}",
        "photo_done": "📸 Все фотографии получены.",
        "submitted": "⏳ Заявка принята. Ожидайте результат.",
        "approved": "✅ Фото-контроль пройден.\nСпасибо.",
        "rejected": "❌ Фото-контроль не пройден.\nПричина:\n{reason}",
        "need_photos": "Нужно отправить ровно 3 фото.",
        "cancel": "❌ Операция отменена.",
        "buttons": {
            "start": "▶️ Начать",
            "finish": "✅ Завершить",
            "cancel": "❌ Отменить"
        }
    },
    "uz": {
        "choose_lang": "Tilni tanlang / Выберите язык",
        "start_info": (
            "ℹ️ Brendlangan avtomobil uchun foto-nazorat\n\n"
            "Tekshiruv uchun:\n"
            "1️⃣ F.I.Sh.\n"
            "2️⃣ Tabel raqami\n"
            "3️⃣ 3 ta fotosurat"
        ),
        "fio": "✍️ 1-bosqich\n\nF.I.Sh. ni kiriting",
        "tab": "🔢 2-bosqich\n\nTabel raqamini kiriting",
        "photo": "📸 3 ta fotosurat yuboring",
        "photo_left": "📸 Qabul qilindi. Qolgan: {n}",
        "photo_done": "📸 Barcha fotosuratlar qabul qilindi.",
        "submitted": "⏳ Ariza qabul qilindi.",
        "approved": "✅ Foto-nazoratdan o‘tildi.",
        "rejected": "❌ O‘tilmadi.\nSabab:\n{reason}",
        "need_photos": "3 ta fotosurat kerak.",
        "cancel": "❌ Bekor qilindi.",
        "buttons": {
            "start": "▶️ Boshlash",
            "finish": "✅ Yakunlash",
            "cancel": "❌ Bekor qilish"
        }
    }
}

# ========== HELPERS ==========
def kb(btns):
    return {"keyboard": [[{"text": b}] for b in btns], "resize_keyboard": True}

def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)

def send_start(chat_id, lang="ru"):
    send(chat_id, TEXTS[lang]["start_info"], kb([TEXTS[lang]["buttons"]["start"]]))
    user_states[chat_id] = "READY"
    user_data[chat_id] = {"lang": lang, "photos": []}

def download_file(file_id):
    info = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    path = info["result"]["file_path"]
    return requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}").content

# ========== TELEGRAM ==========
@app.route("/webhook", methods=["POST"])
def telegram():
    msg = (request.json or {}).get("message")
    if not msg:
        return "ok"

    cid = msg["chat"]["id"]
    txt = msg.get("text", "")
    photos = msg.get("photo")

    data = user_data.get(cid)
    state = user_states.get(cid)

    if txt == "/start" or not data:
        user_states[cid] = "LANG"
        user_data[cid] = {}
        send(cid, TEXTS["ru"]["choose_lang"], kb(["Русский 🇷🇺", "O‘zbek 🇺🇿"]))
        return "ok"

    # LANGUAGE
    if state == "LANG":
        lang = "uz" if "O‘zbek" in txt else "ru"
        send_start(cid, lang)
        return "ok"

    lang = user_data[cid]["lang"]
    btn = TEXTS[lang]["buttons"]

    # READY
    if state == "READY" and txt == btn["start"]:
        user_states[cid] = "WAIT_FIO"
        send(cid, TEXTS[lang]["fio"], kb([btn["cancel"]]))
        return "ok"

    # WAIT_FIO
    if state == "WAIT_FIO":
        if txt == btn["cancel"]:
            send_start(cid, lang)
            return "ok"
        user_data[cid]["fio"] = txt
        user_states[cid] = "WAIT_TAB"
        send(cid, TEXTS[lang]["tab"], kb([btn["cancel"]]))
        return "ok"

    # WAIT_TAB
    if state == "WAIT_TAB":
        if txt == btn["cancel"]:
            send_start(cid, lang)
            return "ok"
        user_data[cid]["tab"] = txt
        user_states[cid] = "WAIT_PHOTO"
        user_data[cid]["photos"] = []
        send(cid, TEXTS[lang]["photo"], kb([btn["cancel"]]))
        return "ok"

    # WAIT_PHOTO
    if state == "WAIT_PHOTO":
        if txt == btn["cancel"]:
            send_start(cid, lang)
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
            send(cid, TEXTS[lang]["submitted"])
            send_start(cid, lang)
            return "ok"

    return "ok"

# ========== ASANA WEBHOOK ==========
@app.route("/asana", methods=["GET", "POST"])
def asana():
    secret = request.headers.get("X-Hook-Secret")
    if secret:
        r = make_response("")
        r.headers["X-Hook-Secret"] = secret
        return r
    return "ok"

@app.route("/")
def root():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))










