import os
import asyncio
import random
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Set

from fastapi.middleware.cors import CORSMiddleware
import database
database.init_db()

from fyers_service import fyers_service
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

# Mobile & Desktop Access Allowed Anywhere

# Models
class FyersConnectRequest(BaseModel):
    app_id: str
    access_token: Optional[str] = ""
    secret_key: Optional[str] = ""
    auth_code: Optional[str] = ""
    redirect_uri: Optional[str] = "https://trade.fyers.in/api-login/redirect-uri/index.html"

class FyersAuthUrlRequest(BaseModel):
    app_id: str
    secret_key: Optional[str] = ""
    redirect_uri: Optional[str] = "https://trade.fyers.in/api-login/redirect-uri/index.html"

class AngelConnectRequest(BaseModel):
    api_key: Optional[str] = ""
    client_id: Optional[str] = ""
    pin: Optional[str] = ""
    totp_secret: Optional[str] = ""

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

# Indices Cache - Correct Nifty Close: 22363.00
indices_cache = {
    "NIFTY": {"ltp": 22363.00, "close": 22363.00, "chg": 0.00, "chg_pct": 0.00},
    "BANKNIFTY": {"ltp": 51842.80, "close": 51842.80, "chg": 0.00, "chg_pct": 0.00}
}

active_watchlist = scrip_manager.get_default_watchlist_contracts()

# Real-Time WebSocket Infrastructure
connected_websockets: Set[WebSocket] = set()
main_loop = None

async def broadcast_ws_message(msg: dict):
    for ws in list(connected_websockets):
        try:
            await ws.send_json(msg)
        except Exception:
            connected_websockets.discard(ws)

# WebSocket Endpoint for Real-Time Streaming to Browsers
@app.websocket("/ws")
async def websocket_terminal(ws: WebSocket):
    await ws.accept()
    connected_websockets.add(ws)
    try:
        # Send instant initial snapshot
        await ws.send_json({
            "type": "snapshot",
            "indices": indices_cache,
            "watchlist": active_watchlist,
            "prices": fyers_service.live_prices,
            "ws_active": fyers_service.is_authenticated
        })
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        connected_websockets.discard(ws)

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
@app.get("/api/fyers/status")
def get_market_status():
    status = fyers_service.get_market_status()
    status["local_ip"] = "192.168.10.42"
    return status

# Fyers API Connect & Auth Endpoints
@app.post("/api/fyers/connect")
def connect_fyers(req: FyersConnectRequest):
    res = fyers_service.connect(
        app_id=req.app_id,
        access_token=req.access_token,
        secret_key=req.secret_key,
        auth_code=req.auth_code,
        redirect_uri=req.redirect_uri
    )
    return res

@app.post("/api/fyers/generate-auth-url")
def generate_fyers_auth_url(req: FyersAuthUrlRequest):
    res = fyers_service.generate_auth_url(
        app_id=req.app_id,
        secret_key=req.secret_key,
        redirect_uri=req.redirect_uri
    )
    return res

# Legacy Angel One compatibility alias
@app.post("/api/angel/connect")
def connect_angel_legacy(req: dict):
    return {"success": False, "message": "Angel One has been replaced with Fyers API. Please use Fyers API settings."}

def get_fallback_price(name: str, strike: float = 0.0, type_: str = "FUT") -> float:
    name_up = name.upper()
    if "NIFTY" in name_up and "BANK" not in name_up:
        spot = indices_cache.get("NIFTY", {}).get("ltp", 22363.00)
        if type_ == "CE" and strike > 0:
            diff = spot - strike
            return round(max(20.0, diff + 135.0), 2)
        elif type_ == "PE" and strike > 0:
            diff = strike - spot
            return round(max(20.0, diff + 125.0), 2)
        return round(spot + 45.0, 2)
    elif "BANKNIFTY" in name_up or "BANK" in name_up:
        spot = indices_cache.get("BANKNIFTY", {}).get("ltp", 51842.80)
        if type_ == "CE" and strike > 0:
            diff = spot - strike
            return round(max(45.0, diff + 290.0), 2)
        elif type_ == "PE" and strike > 0:
            diff = strike - spot
            return round(max(45.0, diff + 270.0), 2)
        return round(spot + 85.0, 2)
    elif "RELIANCE" in name_up:
        return 2845.50
    elif "INFY" in name_up:
        return 1515.20
    elif "HDFCBANK" in name_up:
        return 1720.80
    elif "SBIN" in name_up:
        return 830.40
    elif "TCS" in name_up:
        return 3940.00
    elif "ICICIBANK" in name_up:
        return 1260.00
    elif "TATAMOTORS" in name_up:
        return 920.00
    elif strike > 0:
        return round(max(15.0, strike * 0.025), 2)
    return 245.0

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
    m_stat = fyers_service.get_market_status()
    enriched = []
    for item in user_wl:
        tok = str(item["token"])
        if tok not in fyers_service.live_prices or fyers_service.live_prices[tok] <= 0:
            base_p = get_fallback_price(item.get("name", ""), item.get("strike", 0.0), item.get("type", "FUT"))
            fyers_service.live_prices[tok] = base_p

        ltp = float(fyers_service.live_prices.get(tok, 150.0))
        if not m_stat.get("is_open"):
            close = ltp
            chg = 0.0
            chg_pct = 0.0
        else:
            close = float(item.get("close") or (ltp * 0.994))
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
        "watchlist": enriched,
        "market_status": m_stat
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
    return engine.get_grouped_positions(user_id, fyers_service.live_prices)

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
    return engine.exit_strategy_positions(req.strategy_id, req.user_id or 1, fyers_service.live_prices)

@app.post("/api/positions/exit-group")
def exit_group_api(req: ExitGroupRequest):
    return engine.exit_symbol_group(req.symbol, req.user_id or 1, fyers_service.live_prices)

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
    return engine.get_desk_leaderboard(fyers_service.live_prices)

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
        p["ltp"] = float(fyers_service.live_prices.get(tok, p.get("ltp") or p["avg_price"]))
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

# Background Live Quote Polling from Fyers API v3
@app.on_event("startup")
async def start_background_monitor():
    async def live_quote_loop():
        fyers_service.connect()

        while True:
            try:
                m_status = fyers_service.get_market_status()
                is_open = m_status.get("is_open", False)

                if is_open:
                    if fyers_service.is_authenticated:
                        # 1. Fetch Fyers Live Quotes for Indices
                        try:
                            quotes = fyers_service.get_quotes(["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"])
                            if quotes:
                                if "NSE:NIFTY50-INDEX" in quotes:
                                    n_d = quotes["NSE:NIFTY50-INDEX"]
                                    indices_cache["NIFTY"] = {
                                        "ltp": n_d["ltp"],
                                        "close": n_d["close"],
                                        "chg": n_d["chg"],
                                        "chg_pct": n_d["chg_pct"]
                                    }
                                if "NSE:NIFTYBANK-INDEX" in quotes:
                                    bn_d = quotes["NSE:NIFTYBANK-INDEX"]
                                    indices_cache["BANKNIFTY"] = {
                                        "ltp": bn_d["ltp"],
                                        "close": bn_d["close"],
                                        "chg": bn_d["chg"],
                                        "chg_pct": bn_d["chg_pct"]
                                    }
                        except Exception as eq:
                            pass

                        # 2. Watchlist & Active Position Quotes across all users
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

                            fyers_sym_map = {}
                            for item in active_items:
                                tok = str(item["token"])
                                name = item.get("name", "")
                                if name:
                                    f_sym = f"NSE:{name}" if not name.startswith("NSE:") else name
                                    fyers_sym_map[f_sym] = tok

                            if fyers_sym_map:
                                sym_list = list(fyers_sym_map.keys())[:50]
                                f_quotes = fyers_service.get_quotes(sym_list)
                                if f_quotes:
                                    for f_sym, q_data in f_quotes.items():
                                        tok = fyers_sym_map.get(f_sym)
                                        if tok:
                                            fyers_service.live_prices[tok] = q_data["ltp"]
                        except Exception:
                            pass
                    else:
                        # Market is open but Fyers is not authenticated: live simulation during market hours
                        n_step = round(random.choice([-1.5, -0.5, 0.0, 0.5, 1.5]), 2)
                        indices_cache["NIFTY"]["ltp"] = round(indices_cache["NIFTY"]["ltp"] + n_step, 2)
                        indices_cache["NIFTY"]["chg"] = round(indices_cache["NIFTY"]["ltp"] - indices_cache["NIFTY"]["close"], 2)
                        indices_cache["NIFTY"]["chg_pct"] = round((indices_cache["NIFTY"]["chg"] / indices_cache["NIFTY"]["close"]) * 100, 2)

                        bn_step = round(random.choice([-4.0, -1.0, 0.0, 1.0, 4.0]), 2)
                        indices_cache["BANKNIFTY"]["ltp"] = round(indices_cache["BANKNIFTY"]["ltp"] + bn_step, 2)
                        indices_cache["BANKNIFTY"]["chg"] = round(indices_cache["BANKNIFTY"]["ltp"] - indices_cache["BANKNIFTY"]["close"], 2)
                        indices_cache["BANKNIFTY"]["chg_pct"] = round((indices_cache["BANKNIFTY"]["chg"] / indices_cache["BANKNIFTY"]["close"]) * 100, 2)

                        try:
                            c_loop = database.get_connection()
                            cur_loop = c_loop.cursor()
                            cur_loop.execute("""
                                SELECT DISTINCT token, name, strike, type FROM watchlists
                                UNION
                                SELECT DISTINCT token, name, 0 as strike, 'FUT' as type FROM positions
                            """)
                            active_items = [dict(r) for r in cur_loop.fetchall()]
                            c_loop.close()

                            for item in active_items:
                                tok = str(item["token"])
                                cur_p = fyers_service.live_prices.get(tok)
                                if cur_p is None or cur_p <= 0:
                                    cur_p = get_fallback_price(item.get("name", ""), item.get("strike", 0.0), item.get("type", "FUT"))
                                tick = round(random.choice([-0.20, -0.10, -0.05, 0.05, 0.10, 0.20]), 2)
                                fyers_service.live_prices[tok] = max(1.0, round(cur_p + tick, 2))
                        except Exception:
                            pass

                    # 3. Check All Users SL, Target, 3:15 PM MIS & Daily 1% RMS Loss auto square-offs
                    alerts = engine.check_all_users_rms_and_sl(fyers_service.live_prices)
                    if alerts:
                        print(f"[RMS/SL Alerts] {alerts}")

                else:
                    # MARKET CLOSED (Raat / Holidays): Freeze prices completely at official close, NO jitter!
                    indices_cache["NIFTY"]["ltp"] = indices_cache["NIFTY"]["close"]
                    indices_cache["NIFTY"]["chg"] = 0.0
                    indices_cache["NIFTY"]["chg_pct"] = 0.0
                    indices_cache["BANKNIFTY"]["ltp"] = indices_cache["BANKNIFTY"]["close"]
                    indices_cache["BANKNIFTY"]["chg"] = 0.0
                    indices_cache["BANKNIFTY"]["chg_pct"] = 0.0

                    # Ensure tokens have their stable closing price in cache, but DO NOT fluctuate
                    try:
                        c_loop = database.get_connection()
                        cur_loop = c_loop.cursor()
                        cur_loop.execute("""
                            SELECT DISTINCT token, name, strike, type FROM watchlists
                            UNION
                            SELECT DISTINCT token, name, 0 as strike, 'FUT' as type FROM positions
                        """)
                        active_items = [dict(r) for r in cur_loop.fetchall()]
                        c_loop.close()

                        for item in active_items:
                            tok = str(item["token"])
                            if tok not in fyers_service.live_prices or fyers_service.live_prices[tok] <= 0:
                                fyers_service.live_prices[tok] = get_fallback_price(item.get("name", ""), item.get("strike", 0.0), item.get("type", "FUT"))
                    except Exception:
                        pass

            except Exception as e:
                print(f"[Live Loop Error] {e}")

            await asyncio.sleep(2.0)

    # Launch background tasks on startup
    asyncio.create_task(live_quote_loop())

@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()
    print("[Server Startup] FastAPI Event loop captured for real-time WebSocket broadcasting!")

if __name__ == "__main__":
    import uvicorn
    # Dynamically bind to cloud PORT (e.g. Render, Railway) or default to 8080 locally
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
