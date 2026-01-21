from flask import Flask, request, make_response
import requests
import os

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

ADMIN_CHAT_ID = 927536383  # твой chat_id

def send_message(text):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": ADMIN_CHAT_ID, "text": text}
    )

@app.route("/", methods=["GET"])
def index():
    return "Bot is running"

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    return "ok"

@app.route("/asana", methods=["POST"])
def asana_webhook():
    # Подтверждение webhook
    hook_secret = request.headers.get("X-Hook-Secret")
    if hook_secret:
        response = make_response("")
        response.headers["X-Hook-Secret"] = hook_secret
        return response

    data = request.json
    events = data.get("events", [])

    for event in events:
        if event.get("action") != "changed":
            continue

        task = event.get("resource", {})
        task_gid = task.get("gid")
        task_name = task.get("name", "Заявка")

        if not task_gid:
            continue

        # Запрашиваем полные данные задачи
        headers = {"Authorization": f"Bearer {os.environ.get('ASANA_TOKEN')}"}
        task_data = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=headers,
            params={"opt_fields": "custom_fields.name,custom_fields.display_value"}
        ).json()

        fields = task_data.get("data", {}).get("custom_fields", [])

        status = None
        reason = None

        for field in fields:
            if field["name"] == "Статус заявки":
                status = field["display_value"]
            if field["name"] == "Причина отказа":
                reason = field["display_value"]

        if status == "✅ Одобрено":
            send_message(
                f"✅ Заявка одобрена\n\n"
                f"Название: {task_name}"
            )
            return "ok"

        if status == "❌ Отклонено":
            send_message(
                f"❌ Заявка отклонена\n\n"
                f"Название: {task_name}\n"
                f"Причина: {reason or 'не указана'}"
            )
            return "ok"

    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

