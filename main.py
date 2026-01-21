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

# ========= STATE (in-memory) =========
user_states = {}
user_data = {}

# ========= HELPERS =========
def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json=payload,
            timeout=10
        )
    except Exception as e:
        print("Telegram send error:", e)


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
    try:
        resp = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
            headers={"Authorization": f"Bearer {ASANA_TOKEN}"},
            timeout=10
        ).json()

        for story in reversed(resp.get("data", [])):
            if story.get("type") == "comment":
                return story.get("text")
    except Exception as e:
        print("Asana comment error:", e)

    return "не указана"


def create_asana_task(fio, tab, telegram_id, photos):
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}

    # --- получаем custom fields проекта ---
    fields_resp = requests.get(
        f"https://app.asana.com/api/1.0/projects/{ASANA_PROJECT_ID}/custom_field_settings",
        headers=headers,
        timeout=10
    ).json()

    custom_fields = {}
    for item in fields_resp.get("data", []):
        f = item["custom_field"]
        if f["name"] == "Табель №":
            custom_fields[f["gid"]] = tab
        if f["name"] == "Telegram ID":
            custom_fields[f["gid"]] = str(telegram_id)

    # --- создаём approval-задачу ---
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

    # --- прикрепляем фото ---
    for photo in photos:
        requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task['gid']}/attachments",
            headers=headers,
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
            send_message(
                chat_id,
                "Фото получено. Отправьте ещё или нажмите «Готово»",
                keyboard={
                    "inline_keyboard": [
                        [{"text": "✅ Готово", "callback_data": "DONE"}]
                    ]
                }
            )
            return "ok"

    if "callback_query" in data:
        chat_id = data["callback_query"]["from"]["id"]
        if data["callback_query"]["data"] == "DONE":
            d = user_data.get(chat_id)
            if not d or not d.get("photos"):
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
    # --- handshake ---
    secret = request.headers.get("X-Hook-Secret")
    if secret:
        r = make_response("")
        r.headers["X-Hook-Secret"] = secret
        return r

    payload = request.json or {}
    events = payload.get("events", [])

    if not events:
        return "ok"

    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}

    print("ASANA EVENTS:", events)

    for e in events:
        task_gid = e.get("resource", {}).get("gid")
        if not task_gid:
            continue

        # --- всегда запрашиваем реальное состояние задачи ---
        try:
            task_resp = requests.get(
                f"https://app.asana.com/api/1.0/tasks/{task_gid}",
                headers=headers,
                params={
                    "opt_fields": (
                        "name,"
                        "approval_status,"
                        "custom_fields.name,"
                        "custom_fields.display_value"
                    )
                },
                timeout=10
            )
        except Exception as ex:
            print("Asana task fetch error:", ex)
            continue

        if task_resp.status_code != 200:
            continue

        task = task_resp.json().get("data", {})
        approval = task.get("approval_status")
        task_name = task.get("name", "Заявка")

        # --- получаем Telegram ID ---
        courier_tg = None
        for f in task.get("custom_fields", []):
            if f.get("name") == "Telegram ID" and f.get("display_value"):
                try:
                    courier_tg = int(f["display_value"])
                except ValueError:
                    pass

        if not courier_tg:
            print("Telegram ID not found for task", task_gid)
            continue

        # --- финальные статусы ---
        if approval == "approved":
            send_message(
                courier_tg,
                f"✅ Ваша заявка одобрена\n\nЗаявка: {task_name}"
            )

        elif approval in ("rejected", "changes_requested"):
            reason = get_last_comment(task_gid)
            send_message(
                courier_tg,
                f"❌ Ваша заявка отклонена\n\n"
                f"Заявка: {task_name}\n"
                f"Причина: {reason}"
            )

    return "ok"


# ========= HEALTH =========
@app.route("/", methods=["GET"])
def health():
    return "OK"


# ========= RUN =========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)







