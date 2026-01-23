from flask import Flask, request, make_response
import requests
import os
import time
import threading
import re
import hashlib
from openpyxl import load_workbook

app = Flask(__name__)

# =========================================================
# ======================= ENV ==============================
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_PROJECT_ID = os.environ["ASANA_PROJECT_ID"]
ASANA_ASSIGNEE_ID = os.environ["ASANA_ASSIGNEE_ID"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}

REQUIRED_PHOTOS = 3
SLA_SECONDS = 30 * 60
REWARDS_FILE = "data/rewards.xlsx"

# =========================================================
# ======================= STATE =============================
# =========================================================

user_states = {}
user_data = {}
sent_notifications = set()

# =========================================================
# ======================= TEXTS =============================
# =========================================================

TEXTS = {
    "ru": {
        "choose_lang": "🌐 Выберите язык",
        "menu": "Выберите нужный раздел:",
        "menu_buttons": ["📸 Фото-контроль", "🎁 Вознаграждение", "📄 Статус заявки"],

        "start_info": (
            "🚗 *Фото-контроль брендированного автомобиля*\n\n"
            "1️⃣ Введите ФИО\n"
            "2️⃣ Введите табельный номер\n"
            "3️⃣ Отправьте 3 фото автомобиля\n\n"
            "⚠️ Брендинг и госномер должны быть чётко видны."
        ),

        "fio": "✍️ *Шаг 1 из 3*\nВведите ФИО полностью",

        "tab": (
            "🔢 *Шаг 2 из 3*\n"
            "Введите табельный номер\n\n"
            "📌 Пример: `12345`"
        ),
        "tab_invalid": "❌ Табельный номер должен состоять из *5 цифр*.\n📌 Пример: `12345`",

        "photo": "📸 *Шаг 3 из 3*\nОтправьте 3 фото по одному.",

        "photo_duplicate": "❌ Это фото уже было отправлено.\n📸 Сделайте новое фото.",
        "photo_wrong_state": "❌ Сейчас нельзя отправлять фото.\n📸 Нажмите «Начать» и следуйте шагам.",

        "photo_done": "✅ Все фото получены.\nНажмите «Завершить».",

        "submitted": "⏳ *Заявка отправлена*. Материалы переданы на проверку.",
        "wait_result": "⏳ Ваша заявка находится на проверке.",
        "sla_late": "⏳ Проверка занимает больше времени, чем обычно.",

        "approved": "✅ *Фото-контроль пройден*",
        "rejected": "❌ *Фото-контроль не пройден*\nПричина:\n{reason}",
        "need_photos": "❌ Нужно отправить ровно 3 фото.",
        "default_reject": "Причина не указана.",

        "reward_not_found": "🎁 Данные по вознаграждению не найдены.",

        "reward_info": (
            "🎁 *Вознаграждение*\n\n"
            "👤 {fio}\n"
            "📅 Отработано дней: {days}\n"
            "💰 Сумма: {amount}\n\n"
            "🎟 Промокод:\n*{code}*"
        ),

        "status_no_task": "📄 У вас нет активной заявки.",

        "status_text": (
            "📄 *Статус заявки*\n\n"
            "🆔 ID: {gid}\n"
            "⏳ Статус: {status}\n"
            "🕒 Прошло: {minutes} мин."
        ),
        "status_map": {
            "pending": "На проверке",
            "approved": "Одобрено",
            "rejected": "Отклонено"
        },

        "cancelled": "❌ Заявка отменена.",

        "buttons": {
            "start": "▶️ Начать",
            "cancel": "❌ Отменить",
            "finish": "✅ Завершить",
            "cancel_request": "❌ Отменить заявку"
        }
    },

    "uz": {
        "choose_lang": "🌐 Tilni tanlang",
        "menu": "Kerakli bo‘limni tanlang:",
        "menu_buttons": ["📸 Foto-nazorat", "🎁 Mukofot", "📄 Ariza holati"],

        "start_info": (
            "🚗 *Avtomobil foto-nazorati*\n\n"
            "1️⃣ F.I.Sh kiriting\n"
            "2️⃣ Tabel raqamini kiriting\n"
            "3️⃣ 3 ta foto yuboring"
        ),

        "fio": "✍️ *1-bosqich*\nF.I.Sh kiriting",

        "tab": "🔢 *2-bosqich*\n📌 Misol: `12345`",
        "tab_invalid": "❌ Tabel raqami 5 ta raqamdan iborat.\n📌 Misol: `12345`",

        "photo": "📸 *3-bosqich*\n3 ta foto yuboring.",

        "photo_duplicate": "❌ Bu rasm allaqachon yuborilgan.\n📸 Boshqa rasm oling.",
        "photo_wrong_state": "❌ Hozir rasm yuborib bo‘lmaydi.\n📸 «Boshlash» tugmasini bosing.",

        "photo_done": "✅ Barcha foto qabul qilindi.\n«Yakunlash» ni bosing.",

        "submitted": "⏳ *Ariza yuborildi*. Tekshiruv kutilmoqda.",
        "wait_result": "⏳ Ariza tekshiruvda.",
        "sla_late": "⏳ Tekshiruv cho‘zildi.",

        "approved": "✅ Foto-nazoratdan o‘tildi",
        "rejected": "❌ O‘tilmadi\nSabab:\n{reason}",
        "need_photos": "❌ Aniq 3 ta foto kerak.",
        "default_reject": "Sabab ko‘rsatilmagan.",

        "reward_not_found": "🎁 Mukofot topilmadi.",

        "reward_info": (
            "🎁 *Mukofot*\n\n"
            "👤 {fio}\n"
            "📅 Ishlangan kunlar: {days}\n"
            "💰 Summa: {amount}\n\n"
            "🎟 Promokod:\n*{code}*"
        ),

        "status_no_task": "📄 Sizda faol ariza yo‘q.",

        "status_text": (
            "📄 *Ariza holati*\n\n"
            "🆔 ID: {gid}\n"
            "⏳ Holat: {status}\n"
            "🕒 O‘tgan vaqt: {minutes} daqiqa"
        ),
        "status_map": {
            "pending": "Tekshiruvda",
            "approved": "Tasdiqlandi",
            "rejected": "Rad etildi"
        },

        "cancelled": "❌ Ariza bekor qilindi.",

        "buttons": {
            "start": "▶️ Boshlash",
            "cancel": "❌ Bekor qilish",
            "finish": "✅ Yakunlash",
            "cancel_request": "❌ Arizani bekor qilish"
        }
    }
}

# =========================================================
# ======================= HELPERS ===========================
# =========================================================

def kb(buttons):
    return {"keyboard": [[{"text": b}] for b in buttons], "resize_keyboard": True}

def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)

def reset_to_menu(chat_id, lang):
    user_states[chat_id] = "MENU"
    user_data[chat_id] = {"lang": lang}
    send(chat_id, TEXTS[lang]["menu"], kb(TEXTS[lang]["menu_buttons"]))

def download_file(file_id):
    info = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}).json()
    path = info["result"]["file_path"]
    return requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}").content

def photo_progress(count):
    lines = []
    for i in range(1, REQUIRED_PHOTOS + 1):
        if i <= count:
            lines.append(f"📸 Фото {i}/{REQUIRED_PHOTOS} ✅")
        else:
            lines.append(f"📸 Фото {i}/{REQUIRED_PHOTOS} ⏳")
    return "\n".join(lines)

def get_asana_status(task_gid):
    r = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers=ASANA_HEADERS,
        params={"opt_fields": "approval_status"}
    )
    if r.status_code != 200:
        return None
    return r.json()["data"]["approval_status"]

# =========================================================
# ======================= TELEGRAM ==========================
# =========================================================

@app.route("/webhook", methods=["POST"])
def telegram():
    msg = (request.json or {}).get("message")
    if not msg:
        return "ok"

    cid = msg["chat"]["id"]
    txt = msg.get("text")
    photos = msg.get("photo")
    state = user_states.get(cid)
    lang = user_data.get(cid, {}).get("lang", "ru")
    btn = TEXTS[lang]["buttons"]

    if photos and state != "WAIT_PHOTO":
        send(cid, TEXTS[lang]["photo_wrong_state"])
        return "ok"

    if txt == "/start":
        if state == "WAIT_RESULT":
            send(cid, TEXTS[lang]["wait_result"])
            return "ok"
        user_states[cid] = "LANG"
        user_data[cid] = {}
        send(cid, TEXTS["ru"]["choose_lang"], kb(["Русский 🇷🇺", "O‘zbek 🇺🇿"]))
        return "ok"

    if state == "LANG":
        lang = "uz" if "O‘zbek" in txt else "ru"
        reset_to_menu(cid, lang)
        return "ok"

    # ---------- STATUS ----------
    if txt in ("📄 Статус заявки", "📄 Ariza holati"):
        task_gid = user_data.get(cid, {}).get("task_gid")
        if not task_gid:
            send(cid, TEXTS[lang]["status_no_task"])
            return "ok"

        status = get_asana_status(task_gid)
        minutes = int((time.time() - user_data[cid].get("submitted_at", time.time())) / 60)

        send(
            cid,
            TEXTS[lang]["status_text"].format(
                gid=task_gid,
                status=TEXTS[lang]["status_map"].get(status, status),
                minutes=minutes
            )
        )
        return "ok"

    if txt == btn["cancel_request"] and state != "WAIT_RESULT":
        send(cid, TEXTS[lang]["cancelled"])
        reset_to_menu(cid, lang)
        return "ok"

    if state == "MENU":
        if txt in TEXTS[lang]["menu_buttons"]:
            if "Фото" in txt or "Foto" in txt:
                user_states[cid] = "READY"
                send(cid, TEXTS[lang]["start_info"], kb([btn["start"]]))
            else:
                reward = get_reward(cid)
                if not reward:
                    send(cid, TEXTS[lang]["reward_not_found"])
                else:
                    fio, code, amount, days = reward
                    send(cid, TEXTS[lang]["reward_info"].format(
                        fio=fio, code=code, amount=amount, days=days
                    ))
        return "ok"

    if state == "WAIT_RESULT":
        send(cid, TEXTS[lang]["wait_result"])
        return "ok"

    if state == "READY" and txt == btn["start"]:
        user_states[cid] = "WAIT_FIO"
        user_data[cid]["photos"] = []
        user_data[cid]["photo_hashes"] = set()
        send(cid, TEXTS[lang]["fio"], kb([btn["cancel"], btn["cancel_request"]]))
        return "ok"

    if state == "WAIT_FIO":
        user_data[cid]["fio"] = txt
        user_states[cid] = "WAIT_TAB"
        send(cid, TEXTS[lang]["tab"], kb([btn["cancel"], btn["cancel_request"]]))
        return "ok"

    if state == "WAIT_TAB":
        if not re.fullmatch(r"\d{5}", txt):
            send(cid, TEXTS[lang]["tab_invalid"])
            return "ok"
        user_data[cid]["tab"] = txt
        user_states[cid] = "WAIT_PHOTO"
        send(cid, TEXTS[lang]["photo"], kb([btn["cancel"], btn["cancel_request"]]))
        return "ok"

    if state == "WAIT_PHOTO" and photos:
        file_bytes = download_file(photos[-1]["file_id"])
        file_hash = hashlib.md5(file_bytes).hexdigest()

        if file_hash in user_data[cid]["photo_hashes"]:
            send(cid, TEXTS[lang]["photo_duplicate"])
            return "ok"

        user_data[cid]["photo_hashes"].add(file_hash)
        user_data[cid]["photos"].append(file_bytes)

        progress = photo_progress(len(user_data[cid]["photos"]))
        if len(user_data[cid]["photos"]) == REQUIRED_PHOTOS:
            send(cid, progress + "\n\n" + TEXTS[lang]["photo_done"], kb([btn["finish"]]))
        else:
            send(cid, progress)
        return "ok"

    if state == "WAIT_PHOTO" and txt == btn["finish"]:
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
        user_data[cid]["submitted_at"] = time.time()
        user_data[cid]["sla_notified"] = False
        user_states[cid] = "WAIT_RESULT"

        send(cid, TEXTS[lang]["submitted"], {"remove_keyboard": True})
        return "ok"

    return "ok"

# =========================================================
# ======================= REWARDS ===========================
# =========================================================

def get_reward(chat_id):
    if not os.path.exists(REWARDS_FILE):
        return None

    wb = load_workbook(REWARDS_FILE, data_only=True)
    ws = wb.active

    headers = {}
    for i, cell in enumerate(ws[1]):
        headers[str(cell.value).strip()] = i

    for row in ws.iter_rows(min_row=2, values_only=True):
        tg_id = row[headers["Telegram ID"]]
        if tg_id and str(tg_id).strip() == str(chat_id):
            return (
                row[headers["ФИО"]],
                row[headers["Промокод"]],
                row[headers["Сумма"]],
                row[headers["Отработанные дни"]],
            )
    return None

# =========================================================
# ======================= ASANA =============================
# =========================================================

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

    for i, p in enumerate(photos, start=1):
        requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task['gid']}/attachments",
            headers=ASANA_HEADERS,
            files={"file": (f"photo_{i}.jpg", p, "image/jpeg")}
        )

    return task["gid"]

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
                reset_to_menu(chat_id, lang)
        return

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

# =========================================================
# ======================= SLA ===============================
# =========================================================

def sla_monitor():
    while True:
        now = time.time()
        for cid, state in list(user_states.items()):
            if state == "WAIT_RESULT":
                data = user_data.get(cid)
                if not data or data.get("sla_notified"):
                    continue
                if now - data.get("submitted_at", now) > SLA_SECONDS:
                    lang = data.get("lang", "ru")
                    send(cid, TEXTS[lang]["sla_late"])
                    data["sla_notified"] = True
        time.sleep(60)

threading.Thread(target=sla_monitor, daemon=True).start()

@app.route("/")
def root():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
















