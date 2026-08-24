"""
Simple SQLite layer (no ORM needed for this project size).
Handles: users (4 roles), manually-added publications, notifications,
and small key/value metadata like "scholar data last updated at".
"""
import sqlite3
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash
from config import DATABASE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'hod', 'faculty', 'student')),
    name TEXT NOT NULL,
    department TEXT,
    scholar_id TEXT
);

CREATE TABLE IF NOT EXISTS manual_publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scholar_id TEXT NOT NULL,
    title TEXT NOT NULL,
    year TEXT,
    journal TEXT,
    citations INTEGER DEFAULT 0,
    url TEXT,
    added_by_email TEXT,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    scholar_id TEXT,
    created_at TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    department TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    try:
        conn.execute("ALTER TABLE notifications ADD COLUMN department TEXT")
        conn.commit()
    except Exception:
        pass
    conn.close()
    seed_demo_users()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------- SEED DEMO USERS ----------
DEMO_USERS = [
    # email, password, role, name, department, scholar_id
    ("admin@vit.edu.in", "Admin@123", "admin", "System Admin", None, None),
    ("hod.biomedical@vit.edu.in", "Hod@123", "hod", "HOD - Biomedical", "Biomedical", None),
    ("hod.cmpn@vit.edu.in", "Hod@123", "hod", "HOD - CMPN", "CMPN", None),
    ("hod.excs@vit.edu.in", "Hod@123", "hod", "HOD - EXCS", "EXCS", None),
    ("hod.it@vit.edu.in", "Hod@123", "hod", "HOD - IT", "IT", None),
    ("hod.entc@vit.edu.in", "Hod@123", "hod", "HOD - ENTC", "ENTC", None),
    # Note: Shared placeholder student account until real per-student credentials are issued by the college.
    ("student@vit.edu.in", "Student@123", "student", "Demo Student", None, None),
    ("swapnil.sonawane@vit.edu.in", "Faculty@123", "faculty", "Swapnil Sonawane", "CMPN", None),
    ("sanjeev.dwivedi@vit.edu.in", "Faculty@123", "faculty", "Dr. Sanjeev Dwivedi", "CMPN", None),
]


def seed_demo_users():
    conn = get_db()
    cur = conn.cursor()

    try:
        import pandas as pd
        from config import BASE_DIR
        df = pd.read_excel(BASE_DIR / "faculty_metrics.xlsx")
        name_to_id = dict(zip(df["Name"], df["Scholar ID"].astype(str)))
    except Exception:
        name_to_id = {}

    linked = {
        "Swapnil Sonawane": name_to_id.get("Swapnil Sonawane"),
        "Dr. Sanjeev Dwivedi": name_to_id.get("Dr. Sanjeev Dwivedi"),
    }

    for email, pw, role, name, dept, _ in DEMO_USERS:
        scholar_id = linked.get(name)
        existing = cur.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            continue
        cur.execute(
            "INSERT INTO users (email, password_hash, role, name, department, scholar_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (email, generate_password_hash(pw), role, name, dept, scholar_id),
        )
    conn.commit()
    conn.close()


# ---------- USER HELPERS ----------
def get_user_by_email(email):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def create_user(email, password, role, name, department=None, scholar_id=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (email, password_hash, role, name, department, scholar_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (email.lower().strip(), generate_password_hash(password), role, name, department, scholar_id),
    )
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return get_user_by_id(user_id)


# ---------- MANUAL PUBLICATIONS ----------
def add_manual_publication(scholar_id, title, year, journal, url, added_by_email):
    conn = get_db()
    user_row = conn.execute("SELECT department FROM users WHERE email = ?", (added_by_email,)).fetchone()
    user_dept = user_row["department"] if user_row else None

    conn.execute(
        "INSERT INTO manual_publications (scholar_id, title, year, journal, citations, url, added_by_email, added_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
        (scholar_id, title, year, journal, url, added_by_email, now_iso()),
    )
    conn.execute(
        "INSERT INTO notifications (message, scholar_id, created_at, is_read, department) VALUES (?, ?, ?, 0, ?)",
        (f"{added_by_email} manually added a publication: \"{title}\"", scholar_id, now_iso(), user_dept),
    )
    conn.commit()
    conn.close()


def get_manual_publications(scholar_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM manual_publications WHERE scholar_id = ? ORDER BY added_at DESC", (scholar_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_manual_publication(pub_id, scholar_id):
    conn = get_db()
    conn.execute("DELETE FROM manual_publications WHERE id = ? AND scholar_id = ?", (pub_id, scholar_id))
    conn.commit()
    conn.close()


def get_latest_manual_pub_date(scholar_id):
    conn = get_db()
    row = conn.execute(
        "SELECT added_at FROM manual_publications WHERE scholar_id = ? ORDER BY added_at DESC LIMIT 1",
        (scholar_id,)
    ).fetchone()
    conn.close()
    return row["added_at"] if row else None


# ---------- NOTIFICATIONS ----------
def get_notifications(department=None, unread_only=False):
    conn = get_db()
    conditions = []
    params = []
    if unread_only:
        conditions.append("is_read = 0")
    if department:
        conditions.append("department = ?")
        params.append(department)

    q = "SELECT * FROM notifications"
    if conditions:
        q += " WHERE " + " AND ".join(conditions)
    q += " ORDER BY created_at DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_notification_read(notif_id):
    conn = get_db()
    conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notif_id,))
    conn.commit()
    conn.close()


def unread_notification_count(department=None):
    conn = get_db()
    if department:
        n = conn.execute("SELECT COUNT(*) FROM notifications WHERE is_read = 0 AND department = ?", (department,)).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM notifications WHERE is_read = 0").fetchone()[0]
    conn.close()
    return n


# ---------- META (e.g. "scholar data last updated") ----------
def set_meta(key, value):
    conn = get_db()
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                 (key, value, value))
    conn.commit()
    conn.close()


def get_meta(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default
