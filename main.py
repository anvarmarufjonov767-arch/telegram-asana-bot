from flask import Flask, request, make_response
import requests
import os

app = Flask(__name__)

# ===== ENV =====
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ASANA_TOKEN = os.environ.get("ASANA_TOKEN")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ADMIN_CHAT_ID = 927536383  # твой chat_id


# ===== HELPERS =====
def send_message(text: str):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": ADMIN_CHAT_ID,
            "text": text
        },
        timeout=10
    )


def extract_fio(notes: str) -> str:
    """
    Надёжно извлекает ФИО из описания задачи Asana.
    Поддерживает форматы:
    ФИО:
    Анвар Маруфжонов

    ФИО: Анвар Маруфжонов
    FIO:
    """
    if not notes:
        return "не указано"

    lines = [l.strip() for l in notes.splitlines() if l.strip()]

    for i, line in enumerate(lines):
        key = line.lower().replace(" ", "")
        if key in ["фио", "фио:", "fio", "fio:"]:
            if i + 1 < len(lines):
                return lines[i + 1]
    return "не указано"


def extract_tab_number(custom_fields) -> str:
    """
    Извлекает Табель № из кастомного поля
    """
    for field in custom_fields or []:
        if field.get("name") == "Табель №":
            return field.get("display_value") or "не указан"
    return "не указан"


def get_last_comment(task_gid: str, headers) -> str:
    """
    Возвращает последний комментарий задачи
    """
    resp = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
        headers=headers,
        timeout=10
    ).json()

    for story in reversed(resp.get("data", [])):
        if story.get("type") == "comment":
            return story.get("text")
    return "не указана"


# ===== ROUTES =====
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

        # Получаем задачу
        task_resp = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=headers,
            params={
                "opt_fields": "name,notes,approval_status,custom_fields.name,custom_fields.display_value"
            },
            timeout=10
        ).json()

        task_data = task_resp.get("data", {})
        task_name = task_data.get("name", "Заявка")
        approval_status = task_data.get("approval_status")
        notes = task_data.get("notes", "")
        custom_fields = task_data.get("custom_fields", [])

        fio = extract_fio(notes)
        tab_number = extract_tab_number(custom_fields)

        # ===== APPROVED =====
        if approval_status == "approved":
            send_message(
                f"✅ Заявка одобрена\n\n"
                f"ФИО: {fio}\n"
                f"Табель №: {tab_number}\n"
                f"Заявка: {task_name}"
            )
            break

        # ===== REJECTED / CHANGES =====
        if approval_status in ["rejected", "changes_requested"]:
            reason = get_last_comment(task_gid, headers)

            send_message(
                f"❌ Заявка отклонена\n\n"
                f"ФИО: {fio}\n"
                f"Табель №: {tab_number}\n"
                f"Заявка: {task_name}\n"
                f"Причина: {reason}"
            )
            break

    return "ok"


# ===== RUN =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)




