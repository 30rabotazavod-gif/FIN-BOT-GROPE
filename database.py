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
                amount     INTEGER NOT NULL,
                currency   TEXT    NOT NULL,
                comment    TEXT,
                raw_text   TEXT,
                msg_id     INTEGER
            )
        """)
        # Добавляем msg_id колонку если таблица уже существовала без неё
        try:
            conn.execute("ALTER TABLE transactions ADD COLUMN msg_id INTEGER")
        except Exception:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()


# ─── НАСТРОЙКИ ───────────────────────────────

def get_setting(key, default=None):
    with _get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with _get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        conn.commit()


def get_start_date():
    return get_setting("start_date") or None


def set_start_date(dt: str):
    set_setting("start_date", dt)


# ─── ТРАНЗАКЦИИ ──────────────────────────────

def add_transaction(user_id, username, amount, currency, comment, raw_text, msg_id=None) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start = get_start_date()
    if start and now[:10] < start:
        return -1
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO transactions (created_at,user_id,username,amount,currency,comment,raw_text,msg_id) VALUES (?,?,?,?,?,?,?,?)",
            (now, user_id, username, amount, currency, comment, raw_text, msg_id),
        )
        conn.commit()
        return cur.lastrowid


def update_transaction(tx_id, amount, currency, comment, raw_text):
    """Обновляет запись после редактирования сообщения в группе."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE transactions SET amount=?, currency=?, comment=?, raw_text=? WHERE id=?",
            (amount, currency, comment, raw_text, tx_id),
        )
        conn.commit()


def delete_transaction(tx_id) -> bool:
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
        conn.commit()
        return cur.rowcount > 0


def edit_transaction_comment(tx_id, new_comment):
    with _get_conn() as conn:
        conn.execute("UPDATE transactions SET comment=? WHERE id=?", (new_comment, tx_id))
        conn.commit()


def get_transaction_by_id(tx_id):
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    return dict(row) if row else None


def get_transaction_by_msg_id(msg_id):
    """Найти запись по ID сообщения в группе (для отслеживания редактирования)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE msg_id=? ORDER BY id DESC LIMIT 1", (msg_id,)
        ).fetchone()
    return dict(row) if row else None


def get_balance(from_date=None, to_date=None) -> dict:
    start = from_date or get_start_date()
    query = "SELECT currency, SUM(amount) as total FROM transactions"
    params = []
    conds  = []
    if start:
        conds.append("created_at >= ?")
        params.append(start + " 00:00:00")
    if to_date:
        conds.append("created_at <= ?")
        params.append(to_date + " 23:59:59")
    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += " GROUP BY currency"
    with _get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return {r["currency"]: r["total"] or 0 for r in rows}


def get_recent_transactions(limit=5, from_date=None) -> list:
    start = from_date or get_start_date()
    query = "SELECT * FROM transactions"
    params = []
    if start:
        query += " WHERE created_at >= ?"
        params.append(start + " 00:00:00")
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_all_transactions(from_date=None, to_date=None) -> list:
    start = from_date or get_start_date()
    query = "SELECT * FROM transactions"
    params = []
    conds  = []
    if start:
        conds.append("created_at >= ?")
        params.append(start + " 00:00:00")
    if to_date:
        conds.append("created_at <= ?")
        params.append(to_date + " 23:59:59")
    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += " ORDER BY created_at ASC"
    with _get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_report(from_date, to_date) -> dict:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE created_at >= ? AND created_at <= ? ORDER BY created_at ASC",
            (from_date + " 00:00:00", to_date + " 23:59:59"),
        ).fetchall()
    txs = [dict(r) for r in rows]

    def calc(currency, positive):
        vals = [r["amount"] for r in txs if r["currency"] == currency]
        if positive:
            return sum(v for v in vals if v > 0)
        return abs(sum(v for v in vals if v < 0))

    return {
        "income_uzs":  calc("UZS", True),
        "expense_uzs": calc("UZS", False),
        "balance_uzs": calc("UZS", True) - calc("UZS", False),
        "income_usd":  calc("USD", True),
        "expense_usd": calc("USD", False),
        "balance_usd": calc("USD", True) - calc("USD", False),
        "count":        len(txs),
        "transactions": txs,
    }
