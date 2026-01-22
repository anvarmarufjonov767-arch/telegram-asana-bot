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
user_states = {}
user_data = {}
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
        "photo": "📸 Шаг 3 из 3\nОтправьте 3 фото автомобиля",
        "photo_left": "📸 Фото получены. Осталось: {n}",
        "photo_done": "📸 Все фотографии получены.",
        "submitted": "⏳ Заявка принята\nМатериалы переданы на проверку.",
        "wait_result": "⏳ Ваша заявка находится на проверке.\nПожалуйста, ожидайте результат.",
        "sla_late": "⏳ Проверка занимает больше времени.\nРезультат будет направлен дополнительно.",
        "approved": "✅ Фото-контроль пройден\nАвтомобиль соответствует требованиям.",
        "rejected": "❌ Фото-контроль не пройден\nПричина:\n{reason}",
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
            "1️⃣ F.I.Sh.\n2️⃣ Tabel raqami\n3️⃣ 3 ta fotosurat\n\n"
            "Natija shu chatga yuboriladi."
        ),
        "fio": "✍️ 1-bosqich\nF.I.Sh. ni kiriting",
        "tab": "🔢 2-bosqich\nTabel raqamini kiriting",
        "photo": "📸 3-bosqich\n3 ta fotosurat yuboring",
        "photo_left": "📸 Qabul qilindi. Qolgan: {n}",
        "photo_done": "📸 Barcha fotosuratlar qabul qilindi.",
        "submitted": "⏳ Ariza qabul qilindi.",
        "wait_result": "⏳ Arizangiz tekshiruvda.\nIltimos, kuting.",
        "sla_late": "⏳ Tekshiruv cho‘zildi.\nNatija keyinroq yuboriladi.",
        "approved": "✅ Foto-nazoratdan o‘tildi.",
        "rejected": "❌ O‘tilmadi.\nSabab:\n{reason}",
        "default_reject": "Sabab ko‘rsatilmagan.",
        "buttons": {
            "start": "▶️ Boshlash",
            "cancel": "❌ Bekor qilish",
            "finish": "✅ Yakunlash"
        }
    }
}

# ================= HELPERS =================
def kb(btns):
    return {"keyboard": [[{"text": b}] for b in btns], "resize_keyboard": True}

def send(cid, text, kb_markup=None):
    payload = {"chat_id": cid, "text": text}
    if kb_markup:
        payload["reply_markup"] = kb_markup
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)

def reset_to_start(cid, lang):
    user_states[cid] = "READY"
    user_data[cid] = {
        "lang": lang,
        "photos": [],
        "photo_done_sent": False,
        "submitted_at": None,
        "sla_notified": False
    }
    send(cid, TEXTS[lang]["start_info"], kb([TEXTS[lang]["buttons"]["start"]]))

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

    # 🔒 BLOCKER
    if state == "WAIT_RESULT":
        lang = user_data[cid]["lang"]
        send(cid, TEXTS[lang]["wait_result"])
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
        send(cid, TEXTS[lang]["photo"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_PHOTO":
        if photos:
            for p in photos[:REQUIRED_PHOTOS - len(user_data[cid]["photos"])]:
                user_data[cid]["photos"].append(download_file(p["file_id"]))

            left = REQUIRED_PHOTOS - len(user_data[cid]["photos"])
            if left > 0:
                send(cid, TEXTS[lang]["photo_left"].format(n=left))
            elif not user_data[cid]["photo_done_sent"]:
                user_data[cid]["photo_done_sent"] = True
                send(cid, TEXTS[lang]["photo_done"], kb([btn["finish"]]))
            return "ok"

        if txt == btn["finish"] and len(user_data[cid]["photos"]) == REQUIRED_PHOTOS:
            user_states[cid] = "WAIT_RESULT"
            user_data[cid]["submitted_at"] = time.time()
            send(cid, TEXTS[lang]["submitted"])
            return "ok"

    return "ok"

# ================= SLA MONITOR =================
def sla_monitor():
    while True:
        now = time.time()
        for cid, state in user_states.items():
            if state == "WAIT_RESULT":
                data = user_data.get(cid)
                if data and not data["sla_notified"] and now - data["submitted_at"] > SLA_SECONDS:
                    send(cid, TEXTS[data["lang"]]["sla_late"])
                    data["sla_notified"] = True
        time.sleep(60)

threading.Thread(target=sla_monitor, daemon=True).start()

@app.route("/")
def root():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))











