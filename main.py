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

from flask import make_response

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

    # Флаг, чтобы отправить ТОЛЬКО ОДНО сообщение
    notified = False

    for event in events:
        # Нас интересуют только изменения
        if event.get("action") != "changed":
            continue

        resource = event.get("resource", {})
        resource_name = resource.get("name", "Заявка")

        # Пока упрощённо: одно уведомление на любое изменение
        if not notified:
            send_message(
                f"📌 Заявка обновлена\n\n"
                f"Заявка: {resource_name}"
            )
            notified = True

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

