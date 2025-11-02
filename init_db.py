import sqlite3
conn = sqlite3.connect("users.db")
cur = conn.cursor()
cur.execute(\"\"\"CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    tg_id INTEGER UNIQUE,
    first_name TEXT,
    last_name TEXT,
    username TEXT,
    added_at TEXT
)\"\"\")
conn.commit()
conn.close()
print("DB initialized")
