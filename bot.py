
import os
import time
import json
import threading
import sqlite3
import requests
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from random import choice
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
REMINDER_HOURS = os.getenv("REMINDER_HOURS", "9,11,14,17,20")  # comma-separated hours
DB_PATH = os.getenv("DB_PATH", "users.db")
MESSAGES_FILE = os.getenv("MESSAGES_FILE", "messages.json")

if not BOT_TOKEN:
    raise RuntimeError("Please set BOT_TOKEN in environment variables")

app = Flask(__name__)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        tg_id INTEGER UNIQUE,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        added_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_user(tg_id, first_name=None, last_name=None, username=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (tg_id, first_name, last_name, username, added_at) VALUES (?, ?, ?, ?, datetime('now'))",
                (tg_id, first_name, last_name, username))
    conn.commit()
    conn.close()

def get_all_user_ids():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT tg_id FROM users")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

def load_messages():
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            else:
                return []
    except Exception as e:
        print("Failed to load messages.json:", e)
        return []

def save_messages(messages):
    with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

def send_message(tg_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": tg_id, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print("Failed to send message", r.status_code, r.text)
    except Exception as e:
        print("Exception sending message:", e)

# Handle incoming updates (simple polling)
offset_file = "offset.txt"

def save_offset(offset):
    try:
        with open(offset_file, "w") as f:
            f.write(str(offset))
    except:
        pass

def load_offset():
    try:
        with open(offset_file, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def process_update(u):
    if "message" not in u:
        return
    m = u["message"]
    chat_id = m["chat"]["id"]
    from_user = m.get("from", {})
    text = m.get("text", "")
    if text is None:
        return
    # register user on any message
    add_user(chat_id, from_user.get("first_name"), from_user.get("last_name"), from_user.get("username"))
    if text.startswith("/start"):
        # send welcome message with reference
        welcome = (
            "💧 *Привет!* Я — бот, который будет напоминать пить воду и присылать короткие заботливые фразы.\n\n"
            "Почему это важно: вода участвует во всех процессах организма — от работы мозга до обмена веществ.\n\n"
            "Рекомендуемая норма — примерно *30–35 мл на каждый килограмм веса* в день (это общая рекомендация).\n\n"
            "Подробнее (на русском):\nhttps://www.rospotrebnadzor.ru/about/info/news/news_details.php?ELEMENT_ID=20392\n\n"
            "Если ты — админ бота, отправь мне команду /admin_help"
        )
        send_message(chat_id, welcome)
    elif text.startswith("/admin_help") and from_user.get("id") == ADMIN_ID:
        admin_text = (
            "Админ-команды:\n"
            "/list_messages — показать все сообщения\n"
            "/add_message <текст> — добавить сообщение\n"
            "/remove_message <index> — удалить сообщение по индексу (начиная с 1)\n"
        )
        send_message(chat_id, admin_text)
    elif text.startswith("/list_messages") and from_user.get("id") == ADMIN_ID:
        msgs = load_messages()
        if not msgs:
            send_message(chat_id, "Список сообщений пустой.")
        else:
            out = \"\\n\\n\".join([f\"{i+1}. {m}\" for i, m in enumerate(msgs)])
            send_message(chat_id, out)
    elif text.startswith("/add_message") and from_user.get("id") == ADMIN_ID:
        parts = text.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(chat_id, "Использование: /add_message Текст сообщения")
        else:
            msgs = load_messages()
            msgs.append(parts[1].strip())
            save_messages(msgs)
            send_message(chat_id, "Сообщение добавлено.")
    elif text.startswith("/remove_message") and from_user.get("id") == ADMIN_ID:
        parts = text.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            send_message(chat_id, "Использование: /remove_message Номер")
        else:
            idx = int(parts[1].strip()) - 1
            msgs = load_messages()
            if 0 <= idx < len(msgs):
                removed = msgs.pop(idx)
                save_messages(msgs)
                send_message(chat_id, f'Удалено: {removed}')
            else:
                send_message(chat_id, "Неверный индекс.")

def polling_loop():
    print("Starting polling loop...")
    offset = load_offset()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    while True:
        try:
            params = {"timeout": 30, "offset": offset + 1} if offset else {"timeout": 30}
            r = requests.get(url, params=params, timeout=40)
            data = r.json()
            if data.get("ok"):
                for u in data.get("result", []):
                    offset = u["update_id"]
                    process_update(u)
                if offset:
                    save_offset(offset)
        except Exception as e:
            print("Polling exception:", e)
        time.sleep(1)

# Scheduler: send reminders at specified hours
def send_reminders():
    messages = load_messages()
    users = get_all_user_ids()
    if not messages or not users:
        return
    for u in users:
        try:
            send_message(u, choice(messages))
        except Exception as e:
            print("Error sending to", u, e)

def setup_scheduler():
    scheduler = BackgroundScheduler()
    hours = [int(h) for h in REMINDER_HOURS.split(",") if h.strip().isdigit()]
    for h in hours:
        scheduler.add_job(send_reminders, 'cron', hour=h, minute=0)
    scheduler.start()
    print("Scheduler started with hours:", hours)

@app.route('/')
def index():
    return jsonify({"status": "water-bot", "time": datetime.utcnow().isoformat()})

@app.route('/healthz')
def healthz():
    return "OK"

if __name__ == '__main__':
    init_db()
    # Ensure messages.json exists
    if not os.path.exists(MESSAGES_FILE):
        default = [
            "Глоток воды — глоток осознанности. Сделай паузу и вдохни.",
            "Пей воду — мозг скажет тебе спасибо!",
            "Твое тело — твой дом. Позаботься о нём: выпей один стакан воды.",
            "Небольшая привычка — большая отдача. Сделай глоток прямо сейчас."
        ]
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)

    # start polling in background thread
    t = threading.Thread(target=polling_loop, daemon=True)
    t.start()

    # start scheduler
    setup_scheduler()

    # Start Flask web server (Render requires listening on PORT)
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
