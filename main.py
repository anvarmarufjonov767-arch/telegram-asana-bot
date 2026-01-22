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
SLA_SECONDS = 30 * 60  # 30 минут

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
            "Отправьте 3 фото автомобиля"
        ),
        "photo_left": "📸 Принято. Осталось: {n}",
        "photo_done": "📸 Все фотографии получены.",
        "submitted": "⏳ Заявка принята и передана на проверку.",
        "wait_result": (
            "⏳ Ваша заявка уже находится на проверке.\n\n"
            "Пожалуйста, ожидайте результат."
        ),
        "sla_late": (
            "⏳ Проверка занимает больше времени.\n\n"
            "Ваша заявка всё ещё находится на рассмотрении."
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
        "default_reject": "Причина не указана проверяющим.",
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
        "photo": "📸 3-bosqich\n3 ta fotosurat yuboring",
        "photo_left": "📸 Qabul qilindi. Qolgan: {n}",
        "photo_done": "📸 Barcha fotosuratlar qabul qilindi.",
        "submitted": "⏳ Ariza tekshiruvga yuborildi.",
        "wait_result": (
            "⏳ Arizangiz allaqachon tekshiruvda.\n\n"
            "Iltimos, kuting."
        ),
        "sla_late": (
            "⏳ Tekshiruv biroz cho‘zildi.\n\n"
            "Ariza ko‘rib chiqilmoqda."
        ),
        "approved": "✅ Foto-nazoratdan o‘tildi.",
        "rejected": "❌ O‘tilmadi.\nSabab:\n{reason}",
        "need_photos": "3 ta fotosurat kerak.",
        "default_reject": "Sabab ko‘rsatilmagan.",
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
    user_data[chat_id] = {
        "lang": lang,
        "photos": [],
        "submitted_at": None,
        "sla_notified": False,
        "photo_done_sent": False
    }
    send(chat_id, TEXTS[lang]["start_info"], kb([TEXTS[lang]["buttons"]["start"]]))

def download_file(file_id):
    info = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    path = info["result"]["file_path"]
    return requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}").content

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

    # ⛔ ЖЁСТКИЙ SLA-БЛОКЕР
    if state == "WAIT_RESULT":
        send(cid, TEXTS[user_data[cid]["lang"]]["wait_result"])
        return "ok"

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
        user_data[cid]["fio"] = txt
        user_states[cid] = "WAIT_TAB"
        send(cid, TEXTS[lang]["tab"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_TAB":
        user_data[cid]["tab"] = txt
        user_states[cid] = "WAIT_PHOTO"
        user_data[cid]["photos"] = []
        user_data[cid]["photo_done_sent"] = False
        send(cid, TEXTS[lang]["photo"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_PHOTO" and photos:
        current = len(user_data[cid]["photos"])
        to_add = min(len(photos), REQUIRED_PHOTOS - current)

        for i in range(to_add):
            user_data[cid]["photos"].append(download_file(photos[i]["file_id"]))

        left = REQUIRED_PHOTOS - len(user_data[cid]["photos"])
        if left > 0:
            send(cid, TEXTS[lang]["photo_left"].format(n=left))
        else:
            if not user_data[cid]["photo_done_sent"]:
                user_data[cid]["photo_done_sent"] = True
                send(cid, TEXTS[lang]["photo_done"], kb([btn["finish"]]))
        return "ok"

    if state == "WAIT_PHOTO" and txt == btn["finish"]:
        if len(user_data[cid]["photos"]) != REQUIRED_PHOTOS:
            send(cid, TEXTS[lang]["need_photos"])
            return "ok"

        user_states[cid] = "WAIT_RESULT"
        user_data[cid]["submitted_at"] = time.time()
        send(cid, TEXTS[lang]["submitted"])
        return "ok"

    return "ok"

# ================= SLA MONITOR =================
def sla_monitor():
    while True:
        now = time.time()
        for cid, state in list(user_states.items()):
            if state == "WAIT_RESULT":
                data = user_data.get(cid)
                if not data:
                    continue
                if not data["sla_notified"] and data["submitted_at"] and now - data["submitted_at"] > SLA_SECONDS:
                    send(cid, TEXTS[data["lang"]]["sla_late"])
                    data["sla_notified"] = True
        time.sleep(60)

threading.Thread(target=sla_monitor, daemon=True).start()

@app.route("/")
def root():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))











