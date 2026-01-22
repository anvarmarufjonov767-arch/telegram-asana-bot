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
# (ТВОИ тексты — БЕЗ ИЗМЕНЕНИЙ)
TEXTS = { ... }  # ← оставляем как есть, ты их уже проверил

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
        "photo_done_sent": False,
        "submitted_at": None,
        "sla_notified": False
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

    # /start — ВСЕГДА разрешён
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

    if state == "WAIT_PHOTO":
        if photos:
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

        if txt == btn["finish"]:
            if len(user_data[cid]["photos"]) != REQUIRED_PHOTOS:
                send(cid, TEXTS[lang]["need_photos"])
                return "ok"

            # ✅ ВОТ ЗДЕСЬ ASANA ВЫЗЫВАЕТСЯ
            create_asana_task(
                user_data[cid]["fio"],
                user_data[cid]["tab"],
                cid,
                user_data[cid]["photos"],
                lang
            )

            user_states[cid] = "WAIT_RESULT"
            user_data[cid]["submitted_at"] = time.time()
            user_data[cid]["sla_notified"] = False
            send(cid, TEXTS[lang]["submitted"])
            return "ok"

    # 🔒 БЛОКЕР ПОСЛЕ ОТПРАВКИ В ASANA
    if state == "WAIT_RESULT":
        send(cid, TEXTS[lang]["wait_result"])
        return "ok"

    return "ok"

# ================= SLA MONITOR =================
def sla_monitor():
    while True:
        now = time.time()
        for cid, state in list(user_states.items()):
            if state == "WAIT_RESULT":
                data = user_data.get(cid)
                if data and not data["sla_notified"] and data["submitted_at"]:
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
                chat_id = int(f["display_value"])
                send(chat_id, text)
                reset_to_start(chat_id, lang)
        return

@app.route("/")
def root():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))











