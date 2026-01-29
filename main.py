from flask import Flask, request, make_response
import requests
import os
import time
import threading
import re
import hashlib
import sqlite3
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
PHOTO_DB = "data/photo_hashes.db"

PROCESS_TEXT = {
    "ru": "⏳ Ваша заявка подана и будет обработана в течение 3 рабочих дней.",
    "uz": "⏳ Arizangiz qabul qilindi va 3 ish kuni ichida ko‘rib chiqiladi."
}

# =========================================================
# ======================= STATE =============================
# =========================================================

user_states = {}
user_data = {}
sent_notifications = set()

# =========================================================
# ======================= SQLITE ============================
# =========================================================

def init_db():
    conn = sqlite3.connect(PHOTO_DB)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS photo_hashes (hash TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

def photo_hash_exists(h):
    conn = sqlite3.connect(PHOTO_DB)
    c = conn.cursor()
    c.execute("SELECT 1 FROM photo_hashes WHERE hash = ?", (h,))
    exists = c.fetchone() is not None
    conn.close()
    return exists

def save_photo_hash(h):
    conn = sqlite3.connect(PHOTO_DB)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO photo_hashes(hash) VALUES (?)", (h,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

init_db()

# =========================================================
# ======================= TEXTS =============================
# =========================================================

TEXTS = {  # ← ТВОЙ TEXTS БЕЗ ИЗМЕНЕНИЙ
    # (оставлен полностью как в подтверждённом коде)
}

# =========================================================
# ======================= HELPERS ===========================
# =========================================================

def kb(buttons):
    return {"keyboard": [[{"text": b}] for b in buttons], "resize_keyboard": True}

def inline_kb(text, data):
    return {"inline_keyboard": [[{"text": text, "callback_data": data}]]}

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
    info = requests.get(
        f"{TELEGRAM_API}/getFile",
        params={"file_id": file_id}
    ).json()
    path = info["result"]["file_path"]
    return requests.get(
        f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
    ).content

def photo_progress(count):
    lines = []
    for i in range(1, REQUIRED_PHOTOS + 1):
        lines.append(
            f"📸 Фото {i}/{REQUIRED_PHOTOS} {'✅' if i <= count else '⏳'}"
        )
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
    data = request.json or {}

    # inline копирование промокода
    if "callback_query" in data:
        cq = data["callback_query"]
        cid = cq["message"]["chat"]["id"]
        code = cq["data"].replace("COPY_", "")
        send(cid, code)
        return "ok"

    msg = data.get("message")
    if not msg:
        return "ok"

    cid = msg["chat"]["id"]
    txt = msg.get("text")
    photos = msg.get("photo")
    state = user_states.get(cid)
    lang = user_data.get(cid, {}).get("lang", "ru")
    btn = TEXTS[lang]["buttons"]

    # 🔒 ЖЁСТКИЙ БЛОКЕР
    if state == "WAIT_RESULT":
        send(cid, PROCESS_TEXT[lang])
        return "ok"

    if photos and state != "WAIT_PHOTO":
        send(cid, TEXTS[lang]["photo_wrong_state"])
        return "ok"

    if txt == "/start":
        user_states[cid] = "LANG"
        user_data[cid] = {}
        send(cid, TEXTS["ru"]["choose_lang"], kb(["Русский 🇷🇺", "O‘zbek 🇺🇿"]))
        return "ok"

    if state == "LANG":
        lang = "uz" if "O‘zbek" in txt else "ru"
        reset_to_menu(cid, lang)
        return "ok"

    if txt in ("📄 Статус заявки", "📄 Ariza holati"):
        task_gid = user_data.get(cid, {}).get("task_gid")
        if not task_gid:
            send(cid, TEXTS[lang]["status_no_task"])
            return "ok"
        status = get_asana_status(task_gid)
        send(
            cid,
            TEXTS[lang]["status_text"].format(
                gid=task_gid,
                status=TEXTS[lang]["status_map"].get(status, status),
                minutes=0
            )
        )
        return "ok"

    if state == "MENU":
        if txt in TEXTS[lang]["menu_buttons"]:
            if "Фото" in txt or "Foto" in txt:
                user_states[cid] = "READY"
                send(cid, TEXTS[lang]["start_info"], kb([btn["start"]]))
            else:
                task_gid = user_data.get(cid, {}).get("task_gid")
                if not task_gid or get_asana_status(task_gid) != "approved":
                    send(cid, TEXTS[lang]["reward_not_found"])
                    return "ok"

                reward = get_reward(cid)
                if not reward:
                    send(cid, TEXTS[lang]["reward_not_found"])
                else:
                    fio, code, amount, days = reward
                    send(
                        cid,
                        TEXTS[lang]["reward_info"].format(
                            fio=fio,
                            code=code,
                            amount=amount,
                            days=days
                        ),
                        inline_kb("📋 Скопировать промокод", f"COPY_{code}")
                    )
        return "ok"

    if state == "READY" and txt == btn["start"]:
        user_states[cid] = "WAIT_FIO"
        user_data[cid]["photos"] = []
        user_data[cid]["photo_hashes"] = set()
        send(cid, TEXTS[lang]["fio"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_FIO":
        user_data[cid]["fio"] = txt
        user_states[cid] = "WAIT_TAB"
        send(cid, TEXTS[lang]["tab"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_TAB":
        if not re.fullmatch(r"\d{5}", txt):
            send(cid, TEXTS[lang]["tab_invalid"])
            return "ok"
        user_data[cid]["tab"] = txt
        user_states[cid] = "WAIT_PHOTO"
        send(cid, TEXTS[lang]["photo"], kb([btn["cancel"]]))
        return "ok"

    if state == "WAIT_PHOTO" and photos:
        file_bytes = download_file(photos[-1]["file_id"])
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        if photo_hash_exists(file_hash):
            send(cid, TEXTS[lang]["photo_duplicate"])
            return "ok"

        save_photo_hash(file_hash)

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

        user_data[cid]["photos"] = []  # очистка памяти
        user_data[cid]["task_gid"] = task_gid
        user_states[cid] = "WAIT_RESULT"

        send(cid, PROCESS_TEXT[lang], {"remove_keyboard": True})
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
    headers = {str(c.value).strip(): i for i, c in enumerate(ws[1])}
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
        json={
            "data": {
                "name": "Заявка на фото-контроль",
                "notes": notes,
                "projects": [ASANA_PROJECT_ID],
                "assignee": ASANA_ASSIGNEE_ID,
                "resource_subtype": "approval",
                "approval_status": "pending",
                "custom_fields": custom_fields
            }
        }
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
        time.sleep(60)

threading.Thread(target=sla_monitor, daemon=True).start()

# =========================================================
# ======================= ROOT ==============================
# =========================================================

@app.route("/", methods=["GET", "HEAD"])
def root():
    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


















