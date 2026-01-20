from flask import Flask, request
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

from flask import make_response

@app.route("/asana", methods=["POST"])
def asana_webhook():
    # 🔐 Подтверждение webhook Asana
    hook_secret = request.headers.get("X-Hook-Secret")
    if hook_secret:
        response = make_response("")
        response.headers["X-Hook-Secret"] = hook_secret
        return response

    data = request.json
    events = data.get("events", [])

    for event in events:
        resource = event.get("resource", {})
        resource_name = resource.get("name", "Заявка")

        send_message(
            f"📌 Обновление заявки из Asana\n\n"
            f"Заявка: {resource_name}"
        )

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

