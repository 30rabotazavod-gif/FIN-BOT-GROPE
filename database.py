"""
Работает с SQLite через стандартную библиотеку Python.
На Railway данные хранятся в /data/finance.db (постоянный том).
Если том не подключён — хранится рядом с кодом (для локальной разработки).
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "/data/finance.db")


def _get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT    NOT NULL,
                user_id    INTEGER NOT NULL,
                username   TEXT    NOT NULL,
                amount     INTEGER NOT NULL,   -- положительное = доход, отрицательное = расход
                currency   TEXT    NOT NULL,   -- 'UZS' | 'USD'
                comment    TEXT,
                raw_text   TEXT
            )
        """)
        conn.commit()


def add_transaction(user_id, username, amount, currency, comment, raw_text):
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO transactions (created_at, user_id, username, amount, currency, comment, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
                username,
                amount,
                currency,
                comment,
                raw_text,
            ),
        )
        conn.commit()


def get_balance() -> dict:
    """Возвращает {'UZS': int, 'USD': int}."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT currency, SUM(amount) as total FROM transactions GROUP BY currency"
        ).fetchall()
    return {row["currency"]: row["total"] for row in rows}


def get_recent_transactions(limit: int = 5) -> list:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT username, amount, currency, comment, created_at
            FROM transactions
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
