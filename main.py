import os
import requests
from flask import Flask, request, make_response

app = Flask(__name__)

# ================== ENV ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ASANA_TOKEN = os.environ.get("ASANA_TOKEN")
ASANA_PROJECT_ID = os.environ.get("ASANA_PROJECT_ID")
ASANA_ASSIGNEE_ID = os.environ.get("ASANA_ASSIGNEE_ID")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ================== HELPERS ==================
def send_message(chat_id, text):
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text
            },
            timeout=10
        )
    except Exception as e:
        print("Telegram send error:", e)


def get_last_comment(task_gid):
    try:
        resp = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
            headers={"Authorization": f"Bearer {ASANA_TOKEN}"},
            timeout=10
        )
        stories = resp.json().get("data", [])
        for story in reversed(stories):
            if story.get("type") == "comment":
                return story.get("text")
    except Exception as e:
        print("Asana comment error:", e)

    return "не указана"


# ================== ASANA WEBHOOK ==================
@app.route("/asana", methods=["POST"])
def asana_webhook():
    # --- Handshake ---
    secret = request.headers.get("X-Hook-Secret")
    if secret:
        resp = make_response("")
        resp.headers["X-Hook-Secret"] = secret
        return resp

    payload = request.json or {}
    events = payload.get("events", [])

    if not events:
        return "ok"

    headers = {
        "Authorization": f"Bearer {ASANA_TOKEN}"
    }

    for event in events:
        task_gid = event.get("resource", {}).get("gid")
        if not task_gid:
            continue

        # --- Получаем актуальное состояние задачи ---
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
        except Exception as e:
            print("Asana task fetch error:", e)
            continue

        if task_resp.status_code != 200:
            continue

        task = task_resp.json().get("data", {})
        approval_status = task.get("approval_status")
        task_name = task.get("name", "Заявка")

        # --- Достаём Telegram ID ---
        telegram_id = None
        for field in task.get("custom_fields", []):
            if field.get("name") == "Telegram ID" and field.get("display_value"):
                telegram_id = field.get("display_value")

        if not telegram_id:
            print("Telegram ID not found for task:", task_gid)
            continue

        try:
            telegram_id = int(telegram_id)
        except ValueError:
            continue

        # --- Реакция на решение ---
        if approval_status == "approved":
            send_message(
                telegram_id,
                f"✅ Ваша заявка одобрена\n\n"
                f"Заявка: {task_name}"
            )

        elif approval_status in ("rejected", "changes_requested"):
            reason = get_last_comment(task_gid)
            send_message(
                telegram_id,
                f"❌ Ваша заявка отклонена\n\n"
                f"Заявка: {task_name}\n"
                f"Причина: {reason}"
            )

    return "ok"


# ================== HEALTHCHECK ==================
@app.route("/", methods=["GET"])
def health():
    return "OK"


# ================== RUN ==================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)






