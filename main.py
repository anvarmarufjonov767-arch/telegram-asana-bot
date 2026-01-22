from flask import Flask, request, make_response
import requests
import os
import time
import threading
import re

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
        "choose_lang": "🌐 Выберите язык",
        "start_info": (
            "🚗 *Фото-контроль брендированного автомобиля*\n\n"
            "Этот бот нужен для проверки автомобиля перед выездом.\n\n"
            "📌 Порядок действий:\n"
            "1️⃣ Введите ФИО\n"
            "2️⃣ Введите табельный номер\n"
            "3️⃣ Отправьте 3 фото автомобиля\n\n"
            "⚠️ ВАЖНО:\n"
            "• на фото должен быть *чётко виден брендинг*\n"
            "• должен быть *чётко виден госномер*\n"
            "• каждое фото отправляется *отдельным сообщением*\n\n"
            "После отправки заявка уйдёт на проверку.\n"
            "Результат придёт в этот чат."
        ),
        "fio": "✍️ *Шаг 1 из 3*\n\nВведите ФИО полностью\n\nПример:\nИванов Иван Иванович",
        "tab": (
            "🔢 *Шаг 2 из 3*\n\n"
            "Введите табельный номер\n\n"
            "Требования:\n"
            "• только цифры\n"
            "• ровно 5 цифр\n\n"
            "Пример: 12345"
        ),
        "tab_invalid": "❌ Табельный номер введён неверно.\n\nВведите *ровно 5 цифр* без букв.",
        "photo": (
            "📸 *Шаг 3 из 3*\n\n"
            "Отправьте *3 фотографии автомобиля*:\n\n"
            "1️⃣ Авто целиком\n"
            "2️⃣ Чётко виден госномер\n"
            "3️⃣ Чётко виден брендинг\n\n"
            "⚠️ Фото должны быть чёткими и разными.\n"
            "Каждое фото — отдельным сообщением."
        ),
        "photo_left": "📸 Фото принято.\nОсталось отправить: {n}",
        "photo_done": "✅ Все фотографии получены.\n\nНажмите «Завершить», чтобы отправить заявку.",
        "submitted": (
            "⏳ *Заявка отправлена*\n\n"
            "Материалы переданы на проверку.\n"
            "Пожалуйста, ожидайте результат.\n\n"
            "⛔ Пока заявка проверяется, бот будет недоступен."
        ),
        "wait_result": "⏳ Ваша заявка сейчас на проверке.\nПожалуйста, ожидайте результат.",
        "sla_late": (
            "⏳ Проверка занимает больше времени, чем обычно.\n\n"
            "Ваша заявка всё ещё рассматривается.\n"
            "Результат будет отправлен позже."
        ),
        "approved": "✅ *Фото-контроль пройден*\n\nАвтомобиль соответствует требованиям.",
        "rejected": "❌ *Фото-контроль не пройден*\n\nПричина:\n{reason}",
        "need_photos": "❌ Нужно отправить *ровно 3 фотографии*.",
        "default_reject": "Причина не указана проверяющим.",
        "buttons": {
            "start": "▶️ Начать",
            "cancel": "❌ Отменить",
            "finish": "✅ Завершить"
        }
    },
    "uz": {
        "choose_lang": "🌐 Tilni tanlang",
        "start_info": (
            "🚗 *Brendlangan avtomobil uchun foto-nazorat*\n\n"
            "Bu bot avtomobilni tekshirish uchun mo‘ljallangan.\n\n"
            "📌 Qadamlar:\n"
            "1️⃣ F.I.Sh. kiriting\n"
            "2️⃣ Tabel raqamini kiriting\n"
            "3️⃣ 3 ta avtomobil fotosuratini yuboring\n\n"
            "⚠️ MUHIM:\n"
            "• brending aniq ko‘rinishi kerak\n"
            "• davlat raqami aniq ko‘rinishi kerak\n"
            "• har bir foto alohida yuboriladi"
        ),
        "fio": "✍️ *1-bosqich*\n\nF.I.Sh. ni to‘liq kiriting",
        "tab": (
            "🔢 *2-bosqich*\n\n"
            "Tabel raqamini kiriting\n\n"
            "Talablar:\n"
            "• faqat raqamlar\n"
            "• aniq 5 ta raqam\n\n"
            "Misol: 12345"
        ),
        "tab_invalid": "❌ Tabel raqami noto‘g‘ri.\n\nAniq *5 ta raqam* kiriting.",
        "photo": (
            "📸 *3-bosqich*\n\n"
            "3 ta avtomobil fotosuratini yuboring:\n\n"
            "1️⃣ Avtomobil to‘liq\n"
            "2️⃣ Davlat raqami ko‘rinib tursin\n"
            "3️⃣ Brending ko‘rinib tursin\n\n"
            "Har bir foto alohida yuborilishi kerak."
        ),
        "photo_left": "📸 Qabul qilindi.\nQolgan: {n}",
        "photo_done": "✅ Barcha fotosuratlar qabul qilindi.\n\n«Yakunlash» tugmasini bosing.",
        "submitted": (
            "⏳ *Ariza yuborildi*\n\n"
            "Tekshiruvga jo‘natildi.\n"
            "Iltimos, natijani kuting.\n\n"
            "⛔ Tekshiruv tugaguncha bot yopiq."
        ),
        "wait_result": "⏳ Arizangiz tekshiruvda.\nIltimos, kuting.",
        "sla_late": "⏳ Tekshiruv biroz cho‘zildi.\nNatija keyinroq yuboriladi.",
        "approved": "✅ Foto-nazoratdan o‘tildi.",
        "rejected": "❌ O‘tilmadi.\nSabab:\n{reason}",
        "need_photos": "❌ Aniq 3 ta fotosurat kerak.",
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
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
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
        "task_gid": None
    }
    send(chat_id, TEXTS[lang]["start_info"], kb([TEXTS[lang]["buttons"]["start"]]))

def download_file(file_id):
    info = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    path = info["result"]["file_path"]
    return requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}").content

# ================= ASANA =================
def create_asana_task(fio, tab, tg_id, photos, lang):
    notes = f"ФИО:\n{fio}\n\nLANG:{lang}"

    fields = requests.get(
        f"https://app.asana.com/api/1.0/projects/{ASANA_PROJECT_ID}/custom_field_settings",
        headers=ASANA_HEADERS
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
        }}
    ).json()["data"]

    for p in photos:
        requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task['gid']}/attachments",
            headers=ASANA_HEADERS,
            files={"file": p}
        )

    return task["gid"]

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

    return lang, TEXTS[lang]["default_reject"]

# ================= TELEGRAM =================
@app.route("/webhook", methods=["POST"])
def telegram():
    msg = (request.json or {}).get("message")
    if not msg:
        return "ok"

    cid = msg["chat"]["id"]
    txt = msg.get("text")
    photos = msg.get("photo")

    state = user_states.get(cid)

    # 🔒 БЛОК НА ВРЕМЯ ПРОВЕРКИ
    if state == "WAIT_RESULT":
        send(cid, TEXTS[user_data[cid]["lang"]]["wait_result"])
        return "ok"

    if txt == "/start":
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
        if not re.fullmatch(r"\d{5}", txt):
            send(cid, TEXTS[lang]["tab_invalid"])
            return "ok"
        user_data[cid]["tab"] = txt
        user_states[cid] = "WAIT_PHOTO"
        send(cid, TEXTS[lang]["photo"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_PHOTO":
        if txt == btn["cancel"]:
            reset_to_start(cid, lang)
            return "ok"

        if photos:
            user_data[cid]["photos"].append(
                download_file(photos[-1]["file_id"])
            )
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

            task_gid = create_asana_task(
                user_data[cid]["fio"],
                user_data[cid]["tab"],
                cid,
                user_data[cid]["photos"],
                lang
            )

            user_data[cid]["task_gid"] = task_gid
            user_states[cid] = "WAIT_RESULT"
            user_data[cid]["submitted_at"] = time.time()
            user_data[cid]["sla_notified"] = False

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
                if not data or data["sla_notified"]:
                    continue
                if now - data["submitted_at"] > SLA_SECONDS:
                    send(cid, TEXTS[data["lang"]]["sla_late"])
                    data["sla_notified"] = True
        time.sleep(60)

threading.Thread(target=sla_monitor, daemon=True).start()

# ================= ASANA WEBHOOK =================
@app.route("/asana", methods=["GET", "POST"])
def asana():
    secret = request.headers.get("X-Hook-Secret")
    if secret:
        r = make_response("")
        r.headers["X-Hook-Secret"] = secret
        return r

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
            params={"opt_fields": "approval_status,custom_fields.name,custom_fields.display_value"}
        )
        if r.status_code != 200:
            continue

        data = r.json()["data"]
        status = data["approval_status"]
        if status == "pending":
            continue

        key = f"{task_gid}:{status}"
        if key in sent_notifications:
            return
        sent_notifications.add(key)

        lang, reason = get_task_lang_and_comment(task_gid)
        text = TEXTS[lang]["approved"] if status == "approved" else TEXTS[lang]["rejected"].format(reason=reason)

        for f in data["custom_fields"]:
            if f["name"] == "Telegram ID":
                chat_id = int(f["display_value"])
                send(chat_id, text)
                reset_to_start(chat_id, lang)
        return

@app.route("/")
def root():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))










