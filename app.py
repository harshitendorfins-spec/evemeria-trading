import os
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict

from fastapi.middleware.cors import CORSMiddleware
import database
from angel_service import angel_service
import engine
import scrip_manager

app = FastAPI(title="Evemeria Virtual F&O Platform")

# Enable full CORS support
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

database.init_db()

# Mobile & Desktop Access Allowed Anywhere

# Models
class AngelConnectRequest(BaseModel):
    api_key: str
    client_id: str
    pin: str
    totp_secret: str

class StrategyRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    color: Optional[str] = "#387ed1"
    user_id: Optional[int] = 1

class OrderRequest(BaseModel):
    type: str
    name: str
    token: str
    product: str
    qty: int
    order_price: float
    strategy_id: Optional[str] = "strat_general"
    stoploss: Optional[float] = None
    target: Optional[float] = None
    user_id: Optional[int] = 1

class ExitPositionRequest(BaseModel):
    position_id: str
    exit_qty: int
    exit_price: float
    exit_reason: Optional[str] = "MANUAL"
    user_id: Optional[int] = 1

class LoginRequest(BaseModel):
    username: str
    pin: str

class RegisterRequest(BaseModel):
    username: str
    pin: str
    display_name: Optional[str] = None

# Indices Cache
indices_cache = {
    "NIFTY": {"ltp": 22368.05, "close": 22520.45, "chg": -152.40, "chg_pct": -0.68},
    "BANKNIFTY": {"ltp": 54842.80, "close": 55256.65, "chg": -413.85, "chg_pct": -0.75}
}

# Serve Frontend
@app.get("/")
def get_index():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))

# Auth / Users & Self-Registration
@app.get("/api/auth/users")
def get_trader_list():
    return engine.get_all_users()

@app.post("/api/auth/register")
def register_trader(req: RegisterRequest):
    res = database.register_user(req.username, req.pin, req.display_name)
    if res.get("success"):
        # Seed initial default watchlist for this newly registered trader
        new_u_id = res["user"]["id"]
        defs = scrip_manager.get_default_watchlist_contracts()
        for d in defs:
            database.add_user_watchlist_item(
                user_id=new_u_id,
                token=str(d["token"]),
                symbol=d.get("symbol", ""),
                name=d.get("name", ""),
                exchange=d.get("exchange", "NFO"),
                type_=d.get("type", "FUT"),
                strike=float(d.get("strike", 0.0)),
                lot=int(d.get("lot", 1))
            )
    return res

@app.post("/api/auth/login")
def login_trader(req: LoginRequest):
    raw_user = req.username.strip().upper()
    pin = req.pin.strip()

    # Normalise variants like "1", "01", "TRADER1", "TRADER 1", "DESK 1", "DESK01"
    clean = raw_user.replace("DESK", "").replace("TRADER", "").replace(" ", "").strip()
    desk_num = -1
    normalized_user = raw_user
    if clean.isdigit():
        desk_num = int(clean)
        if 1 <= desk_num <= 99:
            normalized_user = f"TRADER{desk_num:02d}"

    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM users 
        WHERE (username = ? OR username = ? OR desk_no = ?) AND pin = ?
    """, (raw_user, normalized_user, desk_num, pin))
    u = cursor.fetchone()
    conn.close()
    if u:
        user_dict = dict(u)
        user_dict["pin_verified"] = pin
        return {"success": True, "user": user_dict}
    return {"success": False, "message": "Invalid User ID or Password/PIN."}

# Status & Market Info
@app.get("/api/status")
def get_market_status():
    status = angel_service.get_market_status()
    status["local_ip"] = "192.168.10.42"
    return status

# Angel One Connect
@app.post("/api/angel/connect")
def connect_angel(req: AngelConnectRequest):
    res = angel_service.connect(req.api_key, req.client_id, req.pin, req.totp_secret)
    return res

# Isolated User-Specific Watchlist & Search
@app.get("/api/watchlist")
def get_live_watchlist(user_id: int = 1):
    user_wl = database.get_user_watchlist(user_id)
    if not user_wl:
        # Seed default watchlist if empty for this user
        defs = scrip_manager.get_default_watchlist_contracts()
        for d in defs:
            database.add_user_watchlist_item(
                user_id=user_id,
                token=str(d["token"]),
                symbol=d.get("symbol", ""),
                name=d.get("name", ""),
                exchange=d.get("exchange", "NFO"),
                type_=d.get("type", "FUT"),
                strike=float(d.get("strike", 0.0)),
                lot=int(d.get("lot", 1))
            )
        user_wl = database.get_user_watchlist(user_id)

    # Attach live quotes
    enriched = []
    for item in user_wl:
        tok = str(item["token"])
        ltp = float(angel_service.live_prices.get(tok, item.get("ltp") or 0.0))
        close = float(item.get("close") or ltp)
        chg = round(ltp - close, 2)
        chg_pct = round((chg / close) * 100, 2) if close else 0.0
        enriched.append({
            "id": f"NFO_{tok}",
            "token": tok,
            "exchange": item.get("exchange", "NFO"),
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "type": item.get("type", "FUT"),
            "strike": item.get("strike", 0.0),
            "lot": item.get("lot", 1),
            "ltp": ltp,
            "close": close,
            "chg": chg,
            "chg_pct": chg_pct
        })

    return {
        "indices": indices_cache,
        "watchlist": enriched
    }

@app.get("/api/search")
def search_scrips(q: str):
    return scrip_manager.search_scrip_tokens(q)

@app.post("/api/watchlist/add")
def add_to_watchlist(req: dict):
    u_id = int(req.get("user_id", 1))
    token = str(req.get("token"))
    symbol = req.get("symbol", "")
    name = req.get("name", "")
    exchange = req.get("exchange", "NFO")
    type_ = req.get("type", "FUT")
    strike = float(req.get("strike", 0.0))
    lot = int(req.get("lot", 1))

    database.add_user_watchlist_item(u_id, token, symbol, name, exchange, type_, strike, lot)
    return {"success": True, "message": f"Added {name} to Watchlist."}

@app.delete("/api/watchlist/{token}")
def delete_from_watchlist(token: str, user_id: int = 1):
    database.delete_user_watchlist_item(user_id, token)
    return {"success": True, "message": "Removed from Watchlist."}

# Strategies API
@app.get("/api/strategies")
def get_all_strategies(user_id: int = 1):
    return engine.get_strategies(user_id)

@app.post("/api/strategies")
def create_new_strategy(req: StrategyRequest):
    return engine.create_strategy(req.name, req.description, req.color, req.user_id or 1)

@app.delete("/api/strategies/{strategy_id}")
def delete_strategy_api(strategy_id: str, user_id: int = 1):
    return {"success": engine.delete_strategy(strategy_id, user_id)}

# Positions API (with Hedge Benefit)
@app.get("/api/positions")
def get_positions(user_id: int = 1):
    return engine.get_grouped_positions(user_id, angel_service.live_prices)

@app.post("/api/positions/exit")
def exit_single_position(req: ExitPositionRequest):
    return engine.exit_position(req.position_id, req.exit_qty, req.exit_price, req.exit_reason, req.user_id or 1)

class ExitStrategyRequest(BaseModel):
    strategy_id: str
    user_id: Optional[int] = 1

class ExitGroupRequest(BaseModel):
    symbol: str
    user_id: Optional[int] = 1

@app.post("/api/positions/exit-strategy")
def exit_strategy_api(req: ExitStrategyRequest):
    return engine.exit_strategy_positions(req.strategy_id, req.user_id or 1, angel_service.live_prices)

@app.post("/api/positions/exit-group")
def exit_group_api(req: ExitGroupRequest):
    return engine.exit_symbol_group(req.symbol, req.user_id or 1, angel_service.live_prices)

# Orders API
@app.get("/api/orders")
def get_orders(user_id: int = 1):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 200", (user_id,))
    orders = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return orders

@app.post("/api/orders")
def place_order_api(req: OrderRequest):
    res = engine.place_order(
        type_=req.type,
        name=req.name,
        token=req.token,
        product=req.product,
        qty=req.qty,
        order_price=req.order_price,
        strategy_id=req.strategy_id or "strat_general",
        stoploss=req.stoploss,
        target=req.target,
        user_id=req.user_id or 1
    )
    return res

# Closed Trades History
@app.get("/api/history")
def get_trade_history(user_id: int = 1):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM closed_trades WHERE user_id = ? ORDER BY id DESC LIMIT 500", (user_id,))
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return trades

# Ledger & Console Statement
@app.get("/api/ledger")
def get_user_ledger_api(user_id: int = 1):
    return engine.get_user_ledger(user_id)

# Funds API
@app.get("/api/funds")
def get_funds_summary(user_id: int = 1):
    acc = engine.get_user_account(user_id)
    hedge_data = engine.calculate_portfolio_hedge_margin(user_id)
    return {"account": acc, "hedge_info": hedge_data}

@app.post("/api/funds/reset")
def reset_funds_api(req: dict):
    admin_pin = str(req.get("admin_pin", "")).strip()
    user_id = req.get("user_id", 1)
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'ADMIN' AND pin = ?", (admin_pin,))
    admin = cursor.fetchone()
    conn.close()
    if not admin:
        raise HTTPException(status_code=403, detail="Unauthorized: Only Admin can reset funds.")
    return engine.reset_user_account(user_id)

# Desk Monitor / Leaderboard for All Desks (Admin Access Only)
@app.get("/api/desk/leaderboard")
def get_desk_leaderboard_api(admin_pin: str = ""):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'ADMIN' AND pin = ?", (admin_pin.strip(),))
    admin = cursor.fetchone()
    conn.close()
    if not admin:
        raise HTTPException(status_code=403, detail="Unauthorized: Only Admin can view Desks Monitor.")
    return engine.get_desk_leaderboard(angel_service.live_prices)

@app.get("/api/admin/all-positions")
def get_admin_all_positions(admin_pin: str = ""):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'ADMIN' AND pin = ?", (admin_pin.strip(),))
    admin = cursor.fetchone()
    if not admin:
        conn.close()
        raise HTTPException(status_code=403, detail="Unauthorized: Only Admin can view All Positions.")

    cursor.execute("""
        SELECT p.*, u.username, u.display_name, u.desk_no 
        FROM positions p 
        JOIN users u ON p.user_id = u.id 
        ORDER BY u.desk_no ASC, p.created_at DESC
    """)
    positions = [dict(r) for r in cursor.fetchall()]
    conn.close()

    for p in positions:
        tok = str(p["token"])
        p["ltp"] = float(angel_service.live_prices.get(tok, p.get("ltp") or p["avg_price"]))
        is_long = p["qty"] > 0
        pnl = (p["ltp"] - p["avg_price"]) * p["qty"] if is_long else (p["avg_price"] - p["ltp"]) * abs(p["qty"])
        p["pnl"] = round(pnl, 2)

    return positions

@app.get("/api/admin/all-closed-trades")
def get_admin_all_closed_trades(admin_pin: str = ""):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'ADMIN' AND pin = ?", (admin_pin.strip(),))
    admin = cursor.fetchone()
    if not admin:
        conn.close()
        raise HTTPException(status_code=403, detail="Unauthorized: Only Admin can view Closed Trades.")

    cursor.execute("""
        SELECT c.*, u.username, u.display_name, u.desk_no 
        FROM closed_trades c 
        JOIN users u ON c.user_id = u.id 
        ORDER BY c.id DESC LIMIT 500
    """)
    trades = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return trades

@app.post("/api/desk/unlock")
def unlock_desk_api(req: dict):
    admin_pin = str(req.get("admin_pin", "")).strip()
    user_id = req.get("user_id", 1)
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'ADMIN' AND pin = ?", (admin_pin,))
    admin = cursor.fetchone()
    if not admin:
        conn.close()
        raise HTTPException(status_code=403, detail="Unauthorized: Only Admin can unlock desks.")
    cursor.execute("UPDATE users SET is_rms_locked = 0, lock_reason = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": f"Trader Desk {user_id} unlocked successfully."}

# Background Live Quote Polling from Angel One SmartAPI
@app.on_event("startup")
async def start_background_monitor():
    async def live_quote_loop():
        angel_service.connect()

        while True:
            try:
                if angel_service.is_authenticated and angel_service.smart_api:
                    # 1. Nifty 50 Index Quote
                    try:
                        n_q = angel_service.smart_api.ltpData("NSE", "Nifty 50", "99926000")
                        if n_q and n_q.get("status") and n_q.get("data"):
                            d = n_q["data"]
                            ltp = float(d.get("ltp", 0.0))
                            close = float(d.get("close", ltp))
                            chg = round(ltp - close, 2)
                            chg_pct = round((chg / close) * 100, 2) if close else 0.0
                            indices_cache["NIFTY"] = {"ltp": ltp, "close": close, "chg": chg, "chg_pct": chg_pct}
                            angel_service.live_prices["99926000"] = ltp
                    except Exception:
                        pass

                    # 2. Bank Nifty Index Quote
                    try:
                        bn_q = angel_service.smart_api.ltpData("NSE", "Nifty Bank", "99926009")
                        if bn_q and bn_q.get("status") and bn_q.get("data"):
                            d = bn_q["data"]
                            ltp = float(d.get("ltp", 0.0))
                            close = float(d.get("close", ltp))
                            chg = round(ltp - close, 2)
                            chg_pct = round((chg / close) * 100, 2) if close else 0.0
                            indices_cache["BANKNIFTY"] = {"ltp": ltp, "close": close, "chg": chg, "chg_pct": chg_pct}
                            angel_service.live_prices["99926009"] = ltp
                    except Exception:
                        pass

                    # 3. Watchlist & Active Position Quotes across all users
                    try:
                        c_loop = database.get_connection()
                        cur_loop = c_loop.cursor()
                        cur_loop.execute("""
                            SELECT DISTINCT token, name FROM watchlists
                            UNION
                            SELECT DISTINCT token, name FROM positions
                        """)
                        active_items = [dict(r) for r in cur_loop.fetchall()]
                        c_loop.close()

                        for item in active_items:
                            try:
                                tok = str(item["token"])
                                q = angel_service.smart_api.ltpData("NFO", item["name"], tok)
                                if q and q.get("status") and q.get("data"):
                                    d = q["data"]
                                    ltp = float(d.get("ltp", 0.0))
                                    angel_service.live_prices[tok] = ltp
                                await asyncio.sleep(0.08)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # 4. Check All Users SL, Target, 3:15 PM MIS & Daily 1% RMS Loss auto square-offs
                    alerts = engine.check_all_users_rms_and_sl(angel_service.live_prices)
                    if alerts:
                        print(f"[RMS/SL Alerts] {alerts}")

            except Exception as e:
                print(f"[Live Loop Error] {e}")

            await asyncio.sleep(1.5)

    asyncio.create_task(live_quote_loop())

if __name__ == "__main__":
    import uvicorn
    # Dynamically bind to cloud PORT (e.g. Render, Railway) or default to 8080 locally
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
