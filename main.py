from flask import Flask, request, make_response
import requests
import os

app = Flask(__name__)

# ========= ENV =========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ASANA_TOKEN = os.environ.get("ASANA_TOKEN")
ASANA_PROJECT_ID = os.environ.get("ASANA_PROJECT_ID")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ADMIN_CHAT_ID = 927536383

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
    file_path = requests.get(
        f"{TELEGRAM_API}/getFile",
        params={"file_id": file_id}
    ).json()["result"]["file_path"]

    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    return requests.get(file_url).content


def create_task_with_attachments(fio, tab, tg_id, photos):
    headers = {
        "Authorization": f"Bearer {ASANA_TOKEN}"
    }

    # Получаем custom field ids
    fields = requests.get(
        f"https://app.asana.com/api/1.0/projects/{ASANA_PROJECT_ID}/custom_field_settings",
        headers=headers
    ).json()["data"]

    custom_fields = {}
    for f in fields:
        if f["custom_field"]["name"] == "Табель №":
            custom_fields[f["custom_field"]["gid"]] = tab
        if f["custom_field"]["name"] == "Telegram ID":
            custom_fields[f["custom_field"]["gid"]] = str(tg_id)

    task = requests.post(
        "https://app.asana.com/api/1.0/tasks",
        headers=headers,
        json={
            "data": {
                "name": "Заявка на фото-контроль",
                "notes": f"ФИО:\n{fio}",
                "projects": [ASANA_PROJECT_ID],
                "approval_status": "pending",
                "custom_fields": custom_fields
            }
        }
    ).json()["data"]

    # Загружаем фото
    for photo in photos:
        requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task['gid']}/attachments",
            headers=headers,
            files={"file": photo}
        )

    return True


# ========= TELEGRAM =========
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.json or {}

    # Сообщения
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        # START
        if text == "/start":
            user_states[chat_id] = "WAIT_FIO"
            user_data[chat_id] = {"photos": []}
            send_message(chat_id, "Здравствуйте 👋\nВведите ваше ФИО")
            return "ok"

        if user_states.get(chat_id) == "WAIT_FIO":
            user_data[chat_id]["fio"] = text
            user_states[chat_id] = "WAIT_TAB"
            send_message(chat_id, "Введите табельный номер")
            return "ok"

        if user_states.get(chat_id) == "WAIT_TAB":
            user_data[chat_id]["tab"] = text
            user_states[chat_id] = "WAIT_PHOTO"
            send_message(chat_id, "📸 Отправьте фото автомобиля (можно несколько)")
            return "ok"

        # Фото
        if "photo" in data["message"]:
            file_id = data["message"]["photo"][-1]["file_id"]
            user_data[chat_id]["photos"].append(download_file(file_id))
            send_message(
                chat_id,
                "Фото получено. Отправьте ещё или нажмите «Готово»",
                keyboard={
                    "inline_keyboard": [[{"text": "✅ Готово", "callback_data": "DONE"}]]
                }
            )
            return "ok"

    # Callback
    if "callback_query" in data:
        chat_id = data["callback_query"]["from"]["id"]
        if data["callback_query"]["data"] == "DONE":
            d = user_data.get(chat_id)
            create_task_with_attachments(
                d["fio"], d["tab"], chat_id, d["photos"]
            )
            send_message(chat_id, "📨 Заявка отправлена на проверку")
            send_message(
                ADMIN_CHAT_ID,
                f"🆕 Новая заявка с фото\nФИО: {d['fio']}\nТабель №: {d['tab']}"
            )
            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)

    return "ok"


# ========= RUN =========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)





