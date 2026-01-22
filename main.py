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

# ========= STATE =========
user_states = {}
user_data = {}

# защита от дублей (на время аптайма)
sent_notifications = set()

# ========= HELPERS =========
def send_message(chat_id, text):
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10
        )
    except Exception as e:
        print("TELEGRAM ERROR:", e)


def download_file(file_id):
    info = requests.get(
        f"{TELEGRAM_API}/getFile",
        params={"file_id": file_id},
        timeout=10
    ).json()

    file_path = info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    return requests.get(file_url, timeout=20).content


def get_last_comment(task_gid):
    r = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
        headers=ASANA_HEADERS,
        timeout=10
    ).json()

    for story in reversed(r.get("data", [])):
        if story.get("type") == "comment":
            return story.get("text")

    return "не указана"


# ========= ASANA TASK CREATION =========
def create_asana_task(fio, tab, telegram_id, photos):
    fields = requests.get(
        f"https://app.asana.com/api/1.0/projects/{ASANA_PROJECT_ID}/custom_field_settings",
        headers=ASANA_HEADERS,
        timeout=10
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
        json={
            "data": {
                "name": "Заявка на фото-контроль",
                "notes": f"ФИО:\n{fio}",
                "projects": [ASANA_PROJECT_ID],
                "assignee": ASANA_ASSIGNEE_ID,
                "resource_subtype": "approval",
                "approval_status": "pending",
                "custom_fields": custom_fields
            }
        },
        timeout=10
    ).json()["data"]

    for photo in photos:
        requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task['gid']}/attachments",
            headers=ASANA_HEADERS,
            files={"file": photo},
            timeout=20
        )


# ========= TELEGRAM WEBHOOK =========
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.json or {}

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "").strip()

        if text == "/start":
            user_states[chat_id] = "WAIT_FIO"
            user_data[chat_id] = {"photos": []}
            send_message(chat_id, "Введите ФИО")
            return "ok"

        if user_states.get(chat_id) == "WAIT_FIO":
            user_data[chat_id]["fio"] = text
            user_states[chat_id] = "WAIT_TAB"
            send_message(chat_id, "Введите табельный номер")
            return "ok"

        if user_states.get(chat_id) == "WAIT_TAB":
            user_data[chat_id]["tab"] = text
            user_states[chat_id] = "WAIT_PHOTO"
            send_message(chat_id, "Отправьте фото")
            return "ok"

        if "photo" in data["message"] and user_states.get(chat_id) == "WAIT_PHOTO":
            file_id = data["message"]["photo"][-1]["file_id"]
            user_data[chat_id]["photos"].append(download_file(file_id))
            send_message(chat_id, "Фото получено. Отправьте ещё или напишите «Готово»")
            return "ok"

        if text.lower() == "готово" and user_states.get(chat_id) == "WAIT_PHOTO":
            d = user_data.get(chat_id)

            if not d["photos"]:
                send_message(chat_id, "Нужно хотя бы одно фото")
                return "ok"

            create_asana_task(d["fio"], d["tab"], chat_id, d["photos"])
            send_message(chat_id, "Заявка отправлена на проверку")

            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)

    return "ok"


# ========= ASANA PROCESSING (RETRY + GUARANTEE) =========
def process_task(task_gid):
    for _ in range(6):  # ~12 секунд
        time.sleep(2)

        r = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=ASANA_HEADERS,
            params={
                "opt_fields": (
                    "name,approval_status,"
                    "custom_fields.name,custom_fields.display_value"
                )
            },
            timeout=10
        )

        if r.status_code != 200:
            continue

        task = r.json()["data"]
        approval = task.get("approval_status")

        if approval == "pending":
            continue

        dedup_key = f"{task_gid}:{approval}"
        if dedup_key in sent_notifications:
            return
        sent_notifications.add(dedup_key)

        courier_tg = None
        for f in task.get("custom_fields", []):
            if f["name"] == "Telegram ID" and f.get("display_value"):
                try:
                    courier_tg = int(f["display_value"])
                except ValueError:
                    pass

        if not courier_tg:
            return

        task_name = task.get("name", "Заявка")

        if approval == "approved":
            send_message(courier_tg, f"✅ Ваша заявка одобрена\n\n{task_name}")
        else:
            reason = get_last_comment(task_gid)
            send_message(
                courier_tg,
                f"❌ Ваша заявка отклонена\n\n{task_name}\nПричина: {reason}"
            )
        return


# ========= ASANA WEBHOOK =========
@app.route("/asana", methods=["GET", "POST"])
def asana_webhook():
    # handshake от Asana
    secret = request.headers.get("X-Hook-Secret")
    if secret:
        r = make_response("")
        r.headers["X-Hook-Secret"] = secret
        return r

    # GET от UptimeRobot / браузера
    if request.method == "GET":
        return "ok"

    # POST от Asana
    data = request.json or {}
    events = data.get("events", [])

    for e in events:
        task_gid = e.get("resource", {}).get("gid")
        if task_gid:
            threading.Thread(
                target=process_task,
                args=(task_gid,),
                daemon=True
            ).start()

    return "ok"


# ========= ROOT (КОСМЕТИКА) =========
@app.route("/")
def index():
    return "OK"


# ========= RUN =========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)









