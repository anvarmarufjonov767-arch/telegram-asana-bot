from flask import Flask, request, make_response
import requests
import os

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ASANA_TOKEN = os.environ.get("ASANA_TOKEN")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ADMIN_CHAT_ID = 927536383  # твой chat_id

def send_message(text):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": ADMIN_CHAT_ID,
            "text": text
        }
    )

@app.route("/", methods=["GET"])
def index():
    return "Bot is running"

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    return "ok"

@app.route("/asana", methods=["POST"])
def asana_webhook():
    # Подтверждение webhook Asana
    hook_secret = request.headers.get("X-Hook-Secret")
    if hook_secret:
        response = make_response("")
        response.headers["X-Hook-Secret"] = hook_secret
        return response

    data = request.json or {}
    events = data.get("events", [])

    for event in events:
        if event.get("action") != "changed":
            continue

        task = event.get("resource", {})
        task_gid = task.get("gid")

        if not task_gid:
            continue

        headers = {
            "Authorization": f"Bearer {ASANA_TOKEN}"
        }

        # Получаем задачу целиком
        task_response = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=headers,
            params={
                "opt_fields": "name,notes,approval_status,custom_fields.name,custom_fields.display_value"
            }
        ).json()

        task_data = task_response.get("data", {})
        task_name = task_data.get("name", "Заявка")
        approval_status = task_data.get("approval_status")
        notes = task_data.get("notes", "")

        # --- ФИО из описания ---
        fio = "не указано"
        for line in notes.splitlines():
            if "ФИО" in line:
                fio = line.split(":", 1)[-1].strip()
                break

        # --- Табель № из кастомного поля ---
        tab_number = "не указан"
        for field in task_data.get("custom_fields", []):
            if field.get("name") == "Табель №":
                tab_number = field.get("display_value") or tab_number

        # --- Одобрено ---
        if approval_status == "approved":
            send_message(
                f"✅ Заявка одобрена\n\n"
                f"ФИО: {fio}\n"
                f"Табель №: {tab_number}\n"
                f"Заявка: {task_name}"
            )
            break

        # --- Отклонено / Запрос изменений ---
        if approval_status in ["rejected", "changes_requested"]:
            stories = requests.get(
                f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
                headers=headers
            ).json()

            reason = "не указана"
            for story in reversed(stories.get("data", [])):
                if story.get("type") == "comment":
                    reason = story.get("text")
                    break

            send_message(
                f"❌ Заявка отклонена\n\n"
                f"ФИО: {fio}\n"
                f"Табель №: {tab_number}\n"
                f"Заявка: {task_name}\n"
                f"Причина: {reason}"
            )
            break

    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)



