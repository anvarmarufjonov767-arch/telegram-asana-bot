from flask import Flask, request, make_response
import requests
import os

app = Flask(__name__)

# ========= ENV =========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ASANA_TOKEN = os.environ.get("ASANA_TOKEN")
ASANA_PROJECT_ID = os.environ.get("ASANA_PROJECT_ID")
ASANA_ASSIGNEE_ID = os.environ.get("ASANA_ASSIGNEE_ID")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ========= STATE =========
user_states = {}
user_data = {}

# ========= HELPERS =========
def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)


def download_file(file_id):
    file_info = requests.get(
        f"{TELEGRAM_API}/getFile",
        params={"file_id": file_id},
        timeout=10
    ).json()
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    return requests.get(file_url, timeout=20).content


def get_last_comment(task_gid):
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
    resp = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
        headers=headers,
        timeout=10
    ).json()

    for story in reversed(resp.get("data", [])):
        if story.get("type") == "comment":
            return story.get("text")
    return "не указана"


def create_asana_task(fio, tab, telegram_id, photos):
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}

    # custom fields
    fields = requests.get(
        f"https://app.asana.com/api/1.0/projects/{ASANA_PROJECT_ID}/custom_field_settings",
        headers=headers
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
        headers={**headers, "Content-Type": "application/json"},
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
            headers=headers,
            files={"file": photo},
            timeout=20
        )


# ========= TELEGRAM =========
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
            send_message(
                chat_id,
                "Фото получено. Отправьте ещё или нажмите «Готово»",
                keyboard={"inline_keyboard": [[{"text": "✅ Готово", "callback_data": "DONE"}]]}
            )
            return "ok"

    if "callback_query" in data:
        chat_id = data["callback_query"]["from"]["id"]
        if data["callback_query"]["data"] == "DONE":
            d = user_data.get(chat_id)
            if not d or not d["photos"]:
                send_message(chat_id, "Нужно хотя бы одно фото")
                return "ok"

            create_asana_task(d["fio"], d["tab"], chat_id, d["photos"])
            send_message(chat_id, "Заявка отправлена на проверку")
            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)

    return "ok"


# ========= ASANA WEBHOOK =========
@app.route("/asana", methods=["POST"])
def asana_webhook():
    # handshake
    secret = request.headers.get("X-Hook-Secret")
    if secret:
        r = make_response("")
        r.headers["X-Hook-Secret"] = secret
        return r

    data = request.json or {}
    events = data.get("events", [])
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}

    for e in events:
        if e.get("action") != "changed":
            continue

        task_gid = e["resource"]["gid"]

        task = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=headers,
            params={
                "opt_fields": "approval_status,custom_fields.name,custom_fields.display_value"
            }
        ).json()["data"]

        approval = task.get("approval_status")
        courier_tg = None

        for f in task.get("custom_fields", []):
            if f["name"] == "Telegram ID":
                courier_tg = int(f["display_value"])

        if not courier_tg:
            continue

        if approval == "approved":
            send_message(courier_tg, "✅ Ваша заявка одобрена")

        if approval in ["rejected", "changes_requested"]:
            reason = get_last_comment(task_gid)
            send_message(
                courier_tg,
                f"❌ Ваша заявка отклонена\nПричина: {reason}"
            )

    return "ok"


# ========= RUN =========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)






