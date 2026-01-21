from flask import Flask, request, make_response
import requests
import os

app = Flask(__name__)

# ================= ENV =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ASANA_TOKEN = os.environ.get("ASANA_TOKEN")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ADMIN_CHAT_ID = 927536383  # твой chat_id

# ============ REGISTRATION STATE ============
user_states = {}
user_data = {}

# ================= HELPERS =================
def send_message(chat_id, text, task_url=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if task_url:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "🔗 Открыть заявку", "url": task_url}]
            ]
        }
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json=payload,
        timeout=10
    )


def extract_fio(notes: str) -> str:
    if not notes:
        return "не указано"

    lines = [l.strip() for l in notes.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        key = line.lower().replace(" ", "")
        if key in ["фио", "фио:", "fio", "fio:"]:
            if i + 1 < len(lines):
                return lines[i + 1]
    return "не указано"


def extract_tab_and_tg(custom_fields):
    tab_number = "не указан"
    telegram_id = None

    for field in custom_fields or []:
        if field.get("name") == "Табель №":
            tab_number = field.get("display_value") or tab_number
        if field.get("name") == "Telegram ID":
            try:
                telegram_id = int(field.get("display_value"))
            except:
                telegram_id = None

    return tab_number, telegram_id


def get_last_comment(task_gid, headers):
    resp = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
        headers=headers,
        timeout=10
    ).json()

    for story in reversed(resp.get("data", [])):
        if story.get("type") == "comment":
            return story.get("text")
    return "не указана"


# ================= ROUTES =================
@app.route("/", methods=["GET"])
def index():
    return "Bot is running"


# -------- Telegram webhook (авто-регистрация) --------
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.json or {}
    if "message" not in data:
        return "ok"

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "").strip()

    # старт регистрации
    if text == "/start":
        user_states[chat_id] = "WAIT_FIO"
        user_data[chat_id] = {}
        send_message(chat_id, "Здравствуйте 👋\nПожалуйста, отправьте ваше ФИО")
        return "ok"

    # ждём ФИО
    if user_states.get(chat_id) == "WAIT_FIO":
        user_data[chat_id]["fio"] = text
        user_states[chat_id] = "WAIT_TAB"
        send_message(chat_id, "Спасибо.\nТеперь отправьте ваш табельный номер")
        return "ok"

    # ждём табель
    if user_states.get(chat_id) == "WAIT_TAB":
        fio = user_data[chat_id]["fio"]
        tab = text

        send_message(
            ADMIN_CHAT_ID,
            f"🆕 Новый курьер зарегистрирован\n\n"
            f"ФИО: {fio}\n"
            f"Табель №: {tab}\n"
            f"Telegram ID: {chat_id}"
        )

        send_message(chat_id, "✅ Регистрация завершена. Спасибо!")

        user_states.pop(chat_id, None)
        user_data.pop(chat_id, None)
        return "ok"

    return "ok"


# -------- Asana webhook --------
@app.route("/asana", methods=["POST"])
def asana_webhook():
    hook_secret = request.headers.get("X-Hook-Secret")
    if hook_secret:
        response = make_response("")
        response.headers["X-Hook-Secret"] = hook_secret
        return response

    data = request.json or {}
    events = data.get("events", [])

    headers = {
        "Authorization": f"Bearer {ASANA_TOKEN}"
    }

    for event in events:
        if event.get("action") != "changed":
            continue

        task = event.get("resource", {})
        task_gid = task.get("gid")
        if not task_gid:
            continue

        task_resp = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=headers,
            params={
                "opt_fields": "name,notes,approval_status,permalink_url,custom_fields.name,custom_fields.display_value"
            },
            timeout=10
        ).json()

        task_data = task_resp.get("data", {})
        task_name = task_data.get("name", "Заявка")
        task_url = task_data.get("permalink_url")
        approval_status = task_data.get("approval_status")
        notes = task_data.get("notes", "")
        custom_fields = task_data.get("custom_fields", [])

        fio = extract_fio(notes)
        tab_number, courier_tg = extract_tab_and_tg(custom_fields)

        # ----- APPROVED -----
        if approval_status == "approved":
            text = (
                "✅ Ваша заявка одобрена\n\n"
                f"ФИО: {fio}\n"
                f"Табель №: {tab_number}"
            )

            if courier_tg:
                send_message(courier_tg, text, task_url)

            send_message(
                ADMIN_CHAT_ID,
                f"📣 Курьеру отправлено:\n\n{text}",
                task_url
            )
            break

        # ----- REJECTED / CHANGES -----
        if approval_status in ["rejected", "changes_requested"]:
            reason = get_last_comment(task_gid, headers)
            text = (
                "❌ Ваша заявка отклонена\n\n"
                f"ФИО: {fio}\n"
                f"Табель №: {tab_number}\n"
                f"Причина: {reason}"
            )

            if courier_tg:
                send_message(courier_tg, text, task_url)

            send_message(
                ADMIN_CHAT_ID,
                f"📣 Курьеру отправлено:\n\n{text}",
                task_url
            )
            break

    return "ok"


# ================= RUN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)




