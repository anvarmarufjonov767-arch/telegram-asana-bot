from flask import Flask, request
import requests
import os

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

users = set()

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)

@app.route("/", methods=["GET"])
def index():
    return "Bot is running"

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.json

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]

        send_message(
            chat_id,
            f"🆔 Ваш chat_id:\n{chat_id}"
        )

    return "ok"


    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if text == "/start":
            users.add(chat_id)

            keyboard = {
                "keyboard": [
                    [{"text": "📩 Отправить тестовое уведомление"}]
                ],
                "resize_keyboard": True
            }

            send_message(
                chat_id,
                "Вы зарегистрированы ✅\nНажмите кнопку ниже для теста:",
                reply_markup=keyboard
            )

        elif text == "📩 Отправить тестовое уведомление":
            send_message(
                chat_id,
                "🔔 Тестовое уведомление\n\nБот работает корректно ✅"
            )

    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
