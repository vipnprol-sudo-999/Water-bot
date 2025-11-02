import os
import time
import json
import threading
import sqlite3
import requests
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone
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

# --------------------- Database ---------------------

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
    )""")
    conn.commit()
    conn.close()

def add_user(tg_id, first_name=None, last_name=None, username=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (tg_id, first_name, last_name, username, added_at) VALUES (?, ?, ?, ?, datetime('now'))",
        (tg_id, first_name, last_name, username)
    )
    conn.commit()
    conn.close()

def get_all_user_ids():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT tg_id FROM users")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

# --------------------- Messages ---------------------

def load_messages():
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print("Failed to load messages.json:", e)
        return []

def save_messages(messages):
    with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

# --------------------- Send message ---------------------

def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def send_message(tg_id, text, label=""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    safe_text = escape_html(text)
    payload = {"chat_id": tg_id, "text": safe_text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"\n⚠ Failed to send message to {tg_id} ({label}):")
            print(f"Status code: {r.status_code}")
            print(f"Response: {r.text}")
            # Если 400, покажем проблемную область
            if r.status_code == 400:
                offset_str = "Can't find end of the entity starting at byte offset "
                if offset_str in r.text:
                    import re
                    m = re.search(offset_str + r"(\d+)", r.text)
                    if m:
                        offset = int(m.group(1))
                        b = safe_text.encode("utf-8")
                        snippet = b[max(offset-10,0):offset+10].decode("utf-8", errors="replace")
                        print(f"Problematic snippet around byte {offset}: ...{snippet}...")
    except Exception as e:
        print(f"Exception sending message to {tg_id} ({label}): {e}")

# --------------------- Polling ---------------------

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

    add_user(chat_id, from_user.get("first_name"), from_user.get("last_name"), from_user.get("username"))

    if text.startswith("/start"):
        welcome = (
            "Привет! Я — бот, который будет напоминать пить воду и присылать короткие заботливые фразы.\n\n"
            "Почему это важно: вода участвует во всех процессах организма — от работы мозга до обмена веществ.\n\n"
            "Рекомендуемая норма — примерно 30–35 мл на каждый килограмм веса в день.\n\n"
            "Если ты — админ бота, отправь мне команду /admin_help"
        )
        send_message(chat_id, welcome, label="/start welcome")

    elif text.startswith("/admin_help") and from_user.get("id") == ADMIN_ID:
        admin_text = (
            "Админ-команды:\n"
            "/list_messages — показать все сообщения\n"
            "/add_message <текст> — добавить сообщение\n"
            "/remove_message <index> — удалить сообщение по индексу (начиная с 1)\n"
        )
        send_message(chat_id, admin_text, label="/admin_help")

    elif text.startswith("/list_messages") and from_user.get("id") == ADMIN_ID:
        msgs = load_messages()
        if not msgs:
            send_message(chat_id, "Список сообщений пустой.", label="/list_messages empty")
        else:
            out = "\n\n".join([f"{i+1}. {m}" for i, m in enumerate(msgs)])
            send_message(chat_id, out, label="/list_messages content")

    elif text.startswith("/add_message") and from_user.get("id") == ADMIN_ID:
        parts = text.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(chat_id, "Использование: /add_message Текст сообщения", label="/add_message error")
        else:
            msgs = load_messages()
            msgs.append(parts[1].strip())
            save_messages(msgs)
            send_message(chat_id, "Сообщение добавлено.", label="/add_message success")

    elif text.startswith("/remove_message") and from_user.get("id") == ADMIN_ID:
        parts = text.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            send_message(chat_id, "Использование: /remove_message Номер", label="/remove_message error")
        else:
            idx = int(parts[1].strip()) - 1
            msgs = load_messages()
            if 0 <= idx < len(msgs):
                removed = msgs.pop(idx)
                save_messages(msgs)
                send_message(chat_id, f'Удалено: {removed}', label=f"/remove_message line {idx+1}")
            else:
                send_message(chat_id, "Неверный индекс.", label="/remove_message invalid")

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

# --------------------- Scheduler ---------------------

def send_reminders():
    messages = load_messages()
    users = get_all_user_ids()
    if not messages or not users:
        return
    for u in users:
        try:
            msg = choice(messages)
            send_message(u, msg, label="reminder")
        except Exception as e:
            print("Error sending to", u, e)

def setup_scheduler():
    scheduler = BackgroundScheduler()
    hours = [int(h) for h in REMINDER_HOURS.split(",") if h.strip().isdigit()]
    for h in hours:
        scheduler.add_job(send_reminders, 'cron', hour=h, minute=0)
    scheduler.start()
    print("Scheduler started with hours:", hours)

# --------------------- Flask ---------------------

@app.route('/')
def index():
    return jsonify({"status": "water-bot", "time": datetime.now(timezone.utc).isoformat()})

@app.route('/healthz')
def healthz():
    return "OK"

# --------------------- Main ---------------------

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
