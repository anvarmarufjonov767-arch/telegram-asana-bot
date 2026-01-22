from flask import Flask, request, make_response
import requests
import os
import time
import threading

app = Flask(__name__)

# ========= ENV =========
BOT_TOKEN = os.environ["BOT_TOKEN"]
ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_PROJECT_ID = os.environ["ASANA_PROJECT_ID"]
ASANA_ASSIGNEE_ID = os.environ["ASANA_ASSIGNEE_ID"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}

REQUIRED_PHOTOS = 3

# ========= STATE =========
user_states = {}
user_data = {}
sent_notifications = set()

# ========= HELPERS =========
def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard

    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json=payload,
        timeout=10
    )


def kb(buttons):
    return {
        "keyboard": [[{"text": b} for b in row] for row in buttons],
        "resize_keyboard": True
    }


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
    r = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
        headers=ASANA_HEADERS,
        timeout=10
    ).json()

    for story in reversed(r.get("data", [])):
        if story.get("type") == "comment":
            return story.get("text")

    return "не указана"


# ========= ASANA TASK =========
def create_asana_task(fio, tab, telegram_id, photos):
    fields = requests.get(
        f"https://app.asana.com/api/1.0/projects/{ASANA_PROJECT_ID}/custom_field_settings",
        headers=ASANA_HEADERS,
        timeout=10
    ).json()["data"]

    custom_fields = {}
    for item in fields:
        f = item["custom_field"]
        if f["name"] == "Табель №":
            custom_fields[f["gid"]] = tab
        if f["name"] == "Telegram ID":
            custom_fields[f["gid"]] = str(telegram_id)

    task = requests.post(
        "https://app.asana.com/api/1.0/tasks",
        headers={**ASANA_HEADERS, "Content-Type": "application/json"},
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

    for photo in photos:
        requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task['gid']}/attachments",
            headers=ASANA_HEADERS,
            files={"file": photo},
            timeout=20
        )


# ========= TELEGRAM WEBHOOK =========
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.json or {}
    if "message" not in data:
        return "ok"

    msg = data["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    photos = msg.get("photo")

    state = user_states.get(chat_id)

    # START
    if text == "/start":
        user_states[chat_id] = "START"
        user_data[chat_id] = {"photos": []}

        send_message(
            chat_id,
            "Фото-контроль брендированного автомобиля\n\n"
            "Для прохождения проверки выполните следующие шаги:\n"
            "1. Укажите ФИО\n"
            "2. Укажите табельный номер\n"
            "3. Отправьте 3 фотографии автомобиля\n\n"
            "После проверки вы получите уведомление о результате.",
            kb([["Начать"]])
        )
        return "ok"

    if state == "START" and text == "Начать":
        user_states[chat_id] = "WAIT_FIO"
        send_message(
            chat_id,
            "Шаг 1 из 3\n\nВведите ФИО полностью\n"
            "(например: Иванов Иван Иванович)",
            kb([["Отменить"]])
        )
        return "ok"

    if state == "WAIT_FIO":
        if text == "Отменить":
            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)
            send_message(chat_id, "Операция отменена.")
            return "ok"

        user_data[chat_id]["fio"] = text
        user_states[chat_id] = "WAIT_TAB"

        send_message(
            chat_id,
            "Шаг 2 из 3\n\nВведите табельный номер",
            kb([["Назад"], ["Отменить"]])
        )
        return "ok"

    if state == "WAIT_TAB":
        if text == "Назад":
            user_states[chat_id] = "WAIT_FIO"
            send_message(chat_id, "Введите ФИО полностью")
            return "ok"

        if text == "Отменить":
            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)
            send_message(chat_id, "Операция отменена.")
            return "ok"

        user_data[chat_id]["tab"] = text
        user_states[chat_id] = "WAIT_PHOTO"

        send_message(
            chat_id,
            "Шаг 3 из 3\n\n"
            "Отправьте 3 фотографии автомобиля.\n\n"
            "Требования:\n"
            "• автомобиль целиком\n"
            "• отчётливо виден государственный номер\n"
            "• отчётливо видна брендировка",
            kb([["Отменить"]])
        )
        return "ok"

    if state == "WAIT_PHOTO":
        if text == "Отменить":
            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)
            send_message(chat_id, "Операция отменена.")
            return "ok"

        if photos:
            if len(user_data[chat_id]["photos"]) >= REQUIRED_PHOTOS:
                send_message(chat_id, "Дополнительные фотографии не требуются.")
                return "ok"

            file_id = photos[-1]["file_id"]
            user_data[chat_id]["photos"].append(download_file(file_id))
            count = len(user_data[chat_id]["photos"])

            if count < REQUIRED_PHOTOS:
                send_message(
                    chat_id,
                    f"Фотография получена.\n\n"
                    f"Необходимо отправить ещё {REQUIRED_PHOTOS - count} фотографию(и)."
                )
            else:
                send_message(
                    chat_id,
                    "Все необходимые фотографии получены.",
                    kb([["Завершить"], ["Отменить"]])
                )
            return "ok"

        if text == "Завершить":
            if len(user_data[chat_id]["photos"]) != REQUIRED_PHOTOS:
                send_message(
                    chat_id,
                    "Для завершения необходимо отправить ровно 3 фотографии."
                )
                return "ok"

            d = user_data[chat_id]
            create_asana_task(d["fio"], d["tab"], chat_id, d["photos"])

            send_message(
                chat_id,
                "Заявка на фото-контроль принята.\n\n"
                "Материалы переданы на проверку.\n"
                "Результат будет направлен в данном чате."
            )

            user_states.pop(chat_id, None)
            user_data.pop(chat_id, None)
            return "ok"

    return "ok"


# ========= ASANA PROCESS =========
def process_task(task_gid):
    for _ in range(6):
        time.sleep(2)

        r = requests.get(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=ASANA_HEADERS,
            params={
                "opt_fields": (
                    "name,approval_status,"
                    "custom_fields.name,custom_fields.display_value"
                )
            },
            timeout=10
        )

        if r.status_code != 200:
            continue

        task = r.json()["data"]
        approval = task.get("approval_status")

        if approval == "pending":
            continue

        dedup_key = f"{task_gid}:{approval}"
        if dedup_key in sent_notifications:
            return
        sent_notifications.add(dedup_key)

        courier_tg = None
        for f in task.get("custom_fields", []):
            if f["name"] == "Telegram ID" and f.get("display_value"):
                try:
                    courier_tg = int(f["display_value"])
                except ValueError:
                    pass

        if not courier_tg:
            return

        task_name = task.get("name", "Заявка")

        if approval == "approved":
            send_message(courier_tg, f"Фото-контроль пройден.\n\n{task_name}")
        else:
            reason = get_last_comment(task_gid)
            send_message(
                courier_tg,
                f"Фото-контроль не пройден.\n\nПричина:\n{reason}"
            )
        return


# ========= ASANA WEBHOOK =========
@app.route("/asana", methods=["GET", "POST"])
def asana_webhook():
    secret = request.headers.get("X-Hook-Secret")
    if secret:
        r = make_response("")
        r.headers["X-Hook-Secret"] = secret
        return r

    if request.method == "GET":
        return "ok"

    data = request.json or {}
    events = data.get("events", [])

    for e in events:
        task_gid = e.get("resource", {}).get("gid")
        if task_gid:
            threading.Thread(
                target=process_task,
                args=(task_gid,),
                daemon=True
            ).start()

    return "ok"


@app.route("/")
def index():
    return "OK"


# ========= RUN =========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)









