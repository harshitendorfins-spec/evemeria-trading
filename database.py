import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Safe migration: ensure user_id exists on tables
    for tbl in ["positions", "orders", "closed_trades", "strategies"]:
        try:
            cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN user_id INTEGER DEFAULT 1")
        except Exception:
            pass

    # Safe migration: ensure role exists on users table
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'trader'")
    except Exception:
        pass

    # 1. Users Table (18 Office Traders + Admin)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        pin TEXT DEFAULT '1234',
        desk_no INTEGER,
        role TEXT DEFAULT 'trader',
        total_capital REAL DEFAULT 10000000.0,
        available_margin REAL DEFAULT 10000000.0,
        used_margin REAL DEFAULT 0.0,
        realised_pnl REAL DEFAULT 0.0,
        is_rms_locked INTEGER DEFAULT 0,
        lock_reason TEXT
    );
    """)

    # Seed 18 Office Traders if not exists
    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'trader'")
    if cursor.fetchone()[0] == 0:
        for i in range(1, 19):
            uname = f"TRADER{i:02d}"
            dname = f"Trader {i:02d} (Desk {i})"
            cursor.execute("""
                INSERT OR IGNORE INTO users (id, username, display_name, pin, desk_no, role, total_capital, available_margin, used_margin, realised_pnl)
                VALUES (?, ?, ?, '1234', ?, 'trader', 10000000.0, 10000000.0, 0.0, 0.0)
            """, (i, uname, dname, i))

    # Seed Admin User (Username: ADMIN, PIN: 9999)
    cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'ADMIN'")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT OR IGNORE INTO users (id, username, display_name, pin, desk_no, role, total_capital, available_margin, used_margin, realised_pnl)
            VALUES (99, 'ADMIN', 'Evemeria Desk Administrator (Master)', '9999', 0, 'admin', 100000000.0, 100000000.0, 0.0, 0.0)
        """)

    # 2. Strategies Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS strategies (
        id TEXT PRIMARY KEY,
        user_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        description TEXT,
        color TEXT DEFAULT '#387ed1',
        created_at TEXT
    );
    """)

    # Seed default strategies
    cursor.execute("SELECT COUNT(*) FROM strategies")
    if cursor.fetchone()[0] == 0:
        default_strategies = [
            ("strat_general", 1, "General Setup", "Discretionary / Unclassified trades", "#387ed1"),
            ("strat_ema", 1, "EMA 9/21 Momentum", "5-min trend following crossover", "#4caf50"),
            ("strat_breakout", 1, "Range Breakout", "Opening range or key level breakout", "#ff9800"),
            ("strat_scalp", 1, "Scalping", "Quick 10-25 points option scalping", "#9c27b0"),
            ("strat_hedge", 1, "Hedging Setup", "Futures + Protective Put / Spread", "#00bcd4")
        ]
        for s_id, u_id, s_name, s_desc, s_color in default_strategies:
            cursor.execute("INSERT INTO strategies (id, user_id, name, description, color, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (s_id, u_id, s_name, s_desc, s_color, datetime.now().isoformat()))

    # 3. Positions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS positions (
        id TEXT PRIMARY KEY,
        user_id INTEGER DEFAULT 1,
        token TEXT,
        symbol TEXT,
        name TEXT,
        product TEXT,
        qty INTEGER,
        lot_size INTEGER,
        avg_price REAL,
        ltp REAL,
        strategy_id TEXT,
        stoploss REAL,
        target REAL,
        used_margin REAL,
        created_date TEXT,
        created_at TEXT
    );
    """)

    # 4. Orders Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY,
        user_id INTEGER DEFAULT 1,
        time TEXT,
        date TEXT,
        type TEXT,
        name TEXT,
        token TEXT,
        product TEXT,
        qty INTEGER,
        order_price TEXT,
        exec_price REAL,
        strategy_id TEXT,
        stoploss REAL,
        target REAL,
        status TEXT,
        exit_reason TEXT
    );
    """)

    # 5. Closed Trades Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS closed_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER DEFAULT 1,
        time TEXT,
        date TEXT,
        name TEXT,
        product TEXT,
        qty INTEGER,
        buy_avg REAL,
        exit_avg REAL,
        pnl REAL,
        strategy_id TEXT,
        exit_reason TEXT
    );
    """)

    # 6. Ledger Table (Zerodha Console Style Statement)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        time TEXT NOT NULL,
        particulars TEXT NOT NULL,
        voucher_type TEXT DEFAULT 'JOURNAL',
        debit REAL DEFAULT 0.0,
        credit REAL DEFAULT 0.0,
        balance REAL NOT NULL
    );
    """)

    # Seed initial 1 Crore credit entry for all 18 users if empty
    cursor.execute("SELECT COUNT(*) FROM ledger")
    if cursor.fetchone()[0] == 0:
        now_date = datetime.now().strftime("%Y-%m-%d")
        now_time = datetime.now().strftime("%H:%M:%S")
        for i in range(1, 19):
            cursor.execute("""
                INSERT INTO ledger (user_id, date, time, particulars, voucher_type, debit, credit, balance)
                VALUES (?, ?, ?, 'Opening Virtual Capital Allocation', 'RECEIPT', 0.0, 10000000.0, 10000000.0)
            """, (i, now_date, now_time))

    # 7. Fyers API Settings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fyers_settings (
        id INTEGER PRIMARY KEY,
        app_id TEXT,
        access_token TEXT,
        secret_key TEXT,
        redirect_uri TEXT,
        is_connected INTEGER DEFAULT 0,
        last_connected TEXT
    );
    """)

    cursor.execute("""
    INSERT OR IGNORE INTO fyers_settings (id, app_id, access_token, secret_key, redirect_uri, is_connected)
    VALUES (1, '', '', '', 'https://trade.fyers.in/api-login/redirect-uri/index.html', 0);
    """)

    # Keep angel_settings table for clean schema compatibility
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS angel_settings (
        id INTEGER PRIMARY KEY,
        api_key TEXT,
        client_id TEXT,
        pin TEXT,
        totp_secret TEXT,
        feed_token TEXT,
        is_connected INTEGER DEFAULT 0,
        last_connected TEXT
    );
    """)

    cursor.execute("""
    INSERT OR IGNORE INTO angel_settings (id, api_key, client_id, pin, totp_secret, is_connected)
    VALUES (1, '', '', '', '', 0);
    """)

    # 8. User-Specific Watchlists Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watchlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL,
        symbol TEXT,
        name TEXT,
        exchange TEXT DEFAULT 'NFO',
        type TEXT DEFAULT 'FUT',
        strike REAL DEFAULT 0.0,
        lot INTEGER DEFAULT 1,
        UNIQUE(user_id, token)
    );
    """)

    conn.commit()
    conn.close()

# Auto-initialize database on import so tables always exist immediately
try:
    init_db()
except Exception as e:
    print(f"[Database Auto-Init Warning] {e}")

def get_user_watchlist(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM watchlists WHERE user_id = ? ORDER BY id ASC", (user_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def add_user_watchlist_item(user_id: int, token: str, symbol: str, name: str, exchange: str = "NFO", type_: str = "FUT", strike: float = 0.0, lot: int = 1):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO watchlists (user_id, token, symbol, name, exchange, type, strike, lot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, str(token), symbol, name, exchange, type_, strike, lot))
    conn.commit()
    conn.close()
    return True

def delete_user_watchlist_item(user_id: int, token: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlists WHERE user_id = ? AND token = ?", (user_id, str(token)))
    conn.commit()
    conn.close()
    return True

def register_user(username: str, pin: str, display_name: str = None):
    username_clean = username.strip().upper()
    pin_clean = str(pin).strip()
    if not username_clean or not pin_clean:
        return {"success": False, "message": "Username and Password/PIN cannot be empty."}

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE username = ?", (username_clean,))
    if cursor.fetchone():
        conn.close()
        return {"success": False, "message": f"User ID '{username_clean}' already exists. Please choose a different Username."}

    # Assign next desk number
    cursor.execute("SELECT MAX(desk_no) FROM users WHERE role = 'trader'")
    row = cursor.fetchone()
    next_desk = (row[0] or 0) + 1 if row else 1

    dname = display_name.strip() if display_name else f"Trader {username_clean} (Desk {next_desk})"

    cursor.execute("""
        INSERT INTO users (username, display_name, pin, desk_no, role, total_capital, available_margin, used_margin, realised_pnl)
        VALUES (?, ?, ?, ?, 'trader', 10000000.0, 10000000.0, 0.0, 0.0)
    """, (username_clean, dname, pin_clean, next_desk))
    new_user_id = cursor.lastrowid

    # Create initial capital allocation ledger entry
    now_date = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")
    cursor.execute("""
        INSERT INTO ledger (user_id, date, time, particulars, voucher_type, debit, credit, balance)
        VALUES (?, ?, ?, 'Opening Virtual Capital Allocation', 'RECEIPT', 0.0, 10000000.0, 10000000.0)
    """, (new_user_id, now_date, now_time))

    conn.commit()

    # Fetch created user
    cursor.execute("SELECT * FROM users WHERE id = ?", (new_user_id,))
    user_record = dict(cursor.fetchone())
    user_record["pin_verified"] = pin_clean
    conn.close()

    return {"success": True, "user": user_record, "message": f"Trader {username_clean} registered successfully with ₹1,00,00,000 Capital!"}

if __name__ == "__main__":
    init_db()
    print("Database schema successfully upgraded with watchlists & user registration!")
