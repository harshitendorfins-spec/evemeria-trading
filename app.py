import os
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Set

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

# In-memory Watchlist State
active_watchlist = []
indices_cache = {
    "NIFTY": {"ltp": 22368.05, "close": 22520.45, "chg": -152.40, "chg_pct": -0.68},
    "BANKNIFTY": {"ltp": 54842.80, "close": 55256.65, "chg": -413.85, "chg_pct": -0.75}
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

def on_angel_tick_received(tick_obj: dict):
    global main_loop, indices_cache
    token = str(tick_obj.get("token", ""))
    ltp = float(tick_obj.get("ltp", 0.0))
    close = float(tick_obj.get("close", ltp))
    chg = round(ltp - close, 2) if close > 0 else 0.0
    chg_pct = round((chg / close) * 100, 2) if close > 0 else 0.0

    if ltp <= 0:
        return

    # Check indices
    if token in ["99926000", "26000"]:
        indices_cache["NIFTY"] = {"ltp": ltp, "close": close, "chg": chg, "chg_pct": chg_pct}
    elif token in ["99926009", "26009"]:
        indices_cache["BANKNIFTY"] = {"ltp": ltp, "close": close, "chg": chg, "chg_pct": chg_pct}

    # Update active watchlist
    for item in active_watchlist:
        if str(item.get("token")) == token:
            item["ltp"] = ltp
            item["close"] = close
            item["chg"] = chg
            item["chg_pct"] = chg_pct

    # Trigger RMS and SL check immediately in real-time
    try:
        engine.check_all_users_rms_and_sl(angel_service.live_prices)
    except Exception:
        pass

    # Broadcast real-time tick to all connected browsers
    if main_loop and main_loop.is_running() and connected_websockets:
        msg = {
            "type": "tick",
            "token": token,
            "ltp": ltp,
            "close": close,
            "chg": chg,
            "chg_pct": chg_pct,
            "indices": indices_cache
        }
        asyncio.run_coroutine_threadsafe(broadcast_ws_message(msg), main_loop)

# Register tick callback with AngelOneService
angel_service.register_tick_callback(on_angel_tick_received)

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
            "prices": angel_service.live_prices,
            "ws_active": angel_service.is_ws_connected
        })
        while True:
            data = await ws.receive_json()
            if data and data.get("action") == "subscribe":
                tokens = data.get("tokens", [])
                if tokens:
                    angel_service.subscribe_tokens(tokens)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        connected_websockets.discard(ws)

# Serve Frontend
@app.get("/")
def get_index():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))

# Auth / 18 Users
@app.get("/api/auth/users")
def get_trader_list():
    return engine.get_all_users()

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
        if 1 <= desk_num <= 18:
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
    return {"success": False, "message": "Invalid User ID or PIN. (Default Trader PIN: 1234, Admin PIN: 9999)"}

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

# Watchlist & Search
@app.get("/api/watchlist")
def get_live_watchlist():
    return {
        "indices": indices_cache,
        "watchlist": active_watchlist
    }

@app.get("/api/search")
def search_scrips(q: str):
    return scrip_manager.search_scrip_tokens(q)

@app.post("/api/watchlist/add")
def add_to_watchlist(req: dict):
    global active_watchlist
    token = str(req.get("token"))
    if not any(str(w.get("token")) == token for w in active_watchlist):
        active_watchlist.append({
            "id": f"NFO_{token}",
            "token": token,
            "exchange": "NFO",
            "symbol": req.get("symbol"),
            "name": req.get("name"),
            "type": req.get("type", "FUT"),
            "strike": req.get("strike", 0.0),
            "lot": req.get("lot", 1),
            "ltp": 0.0,
            "close": 0.0,
            "chg": 0.0,
            "chg_pct": 0.0
        })
    return {"success": True, "watchlist": active_watchlist}

@app.delete("/api/watchlist/{token}")
def delete_from_watchlist(token: str):
    global active_watchlist
    active_watchlist = [w for w in active_watchlist if str(w.get("token")) != str(token)]
    return {"success": True, "watchlist": active_watchlist}

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

@app.post("/api/positions/exit-strategy")
def exit_strategy_api(req: ExitStrategyRequest):
    return engine.exit_strategy_positions(req.strategy_id, req.user_id or 1, angel_service.live_prices)

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

# Desk Monitor / Leaderboard for All 18 Desks (Admin Access Only)
@app.get("/api/desk/leaderboard")
def get_desk_leaderboard_api(admin_pin: str = ""):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'ADMIN' AND pin = ?", (admin_pin.strip(),))
    admin = cursor.fetchone()
    conn.close()
    if not admin:
        raise HTTPException(status_code=403, detail="Unauthorized: Only Admin can view 18 Desks Monitor.")
    return engine.get_desk_leaderboard(angel_service.live_prices)

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
                    except Exception as e:
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
                    except Exception as e:
                        pass

                    # 3. Watchlist Quotes
                    for item in active_watchlist:
                        try:
                            q = angel_service.smart_api.ltpData("NFO", item["name"], str(item["token"]))
                            if q and q.get("status") and q.get("data"):
                                d = q["data"]
                                ltp = float(d.get("ltp", 0.0))
                                close = float(d.get("close", ltp))
                                chg = round(ltp - close, 2)
                                chg_pct = round((chg / close) * 100, 2) if close else 0.0
                                item["ltp"] = ltp
                                item["close"] = close
                                item["chg"] = chg
                                item["chg_pct"] = chg_pct
                                angel_service.live_prices[str(item["token"])] = ltp
                            await asyncio.sleep(0.12)
                        except Exception as e:
                            pass

                    # 4. Check All 18 Users SL, Target & Daily 1% RMS Loss auto square-offs
                    alerts = engine.check_all_users_rms_and_sl(angel_service.live_prices)
                    if alerts:
                        print(f"[RMS/SL Alerts] {alerts}")

            except Exception as e:
                print(f"[Live Loop Error] {e}")

            # If WebSocket is connected and streaming, backup REST poll can rest longer
            sleep_time = 3.0 if angel_service.is_ws_connected else 1.5
            await asyncio.sleep(sleep_time)

    # Launch background tasks on startup
    asyncio.create_task(live_quote_loop())

    # Pre-subscribe default watchlist and index tokens to Angel WebSocket
    initial_tokens = [str(w.get("token")) for w in active_watchlist if w.get("token")]
    angel_service.subscribe_tokens(initial_tokens, exchange_type=2)
    angel_service.subscribe_tokens(["99926000", "99926009", "26000", "26009"], exchange_type=1)

    # Launch WebSocket if authenticated
    if angel_service.is_authenticated:
        angel_service.start_websocket()

@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()
    print("[Server Startup] FastAPI Event loop captured for real-time WebSocket broadcasting!")

if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 so all 18 office desktops on LAN can connect!
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False)
