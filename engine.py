import time
from datetime import datetime
from database import get_connection

MAX_TRADE_RISK = 100000.0   # Max 1% per trade (1 Lakh on 1 Crore)
MAX_DAILY_LOSS = 100000.0   # Max 1% daily MTM loss (1 Lakh on 1 Crore)

def get_user_account(user_id=1):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, username, display_name, desk_no, total_capital, available_margin, 
               used_margin, realised_pnl, is_rms_locked, lock_reason 
        FROM users WHERE id = ?
    """, (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_users():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, display_name, desk_no, role, is_rms_locked FROM users WHERE role = 'trader' ORDER BY desk_no ASC")
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users

def get_strategies(user_id=1):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, description, color, created_at 
        FROM strategies 
        WHERE user_id = ? OR user_id = 1 
        ORDER BY created_at ASC
    """, (user_id,))
    strategies = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return strategies

def create_strategy(name, description="", color="#387ed1", user_id=1):
    strat_id = "strat_" + str(int(time.time()))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO strategies (id, user_id, name, description, color, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (strat_id, user_id, name, description, color, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"id": strat_id, "name": name, "color": color}

def delete_strategy(strategy_id, user_id=1):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE positions SET strategy_id = 'strat_general' WHERE strategy_id = ? AND user_id = ?", (strategy_id, user_id))
    cursor.execute("DELETE FROM strategies WHERE id = ? AND user_id = ?", (strategy_id, user_id))
    conn.commit()
    conn.close()
    return True

# =========================================================================
# HEDGE MARGIN BENEFIT ENGINE (SPAN PORTFOLIO MARGIN)
# =========================================================================
def calculate_single_naked_margin(type_, name, qty, price, lot_size):
    lots = max(1, round(qty / lot_size))
    if type_ == "BUY" and not ("FUT" in name):
        # Option Buying = Premium * Qty
        return price * qty
    else:
        # Option Selling or Futures = Standard SPAN ~1,20,000 / lot
        span_base = 140000.0 if "BANKNIFTY" in name else 120000.0
        return span_base * lots

def calculate_portfolio_hedge_margin(user_id=1):
    """
    Evaluates open positions for offsets/hedges (e.g. Long Future + Long Put, Spreads)
    and calculates proper margin benefits.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM positions WHERE user_id = ?", (user_id,))
    positions = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not positions:
        return {"total_margin": 0.0, "naked_margin": 0.0, "hedge_benefit": 0.0}

    # Group by underlying symbol (e.g. RELIANCE, NIFTY, INFY)
    by_symbol = {}
    for p in positions:
        sym = p["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(p)

    total_naked_margin = 0.0
    total_hedge_discount = 0.0

    for sym, pos_list in by_symbol.items():
        sym_naked = 0.0
        has_long_fut = False
        has_short_fut = False
        long_pe_count = 0
        long_ce_count = 0
        short_pe_count = 0
        short_ce_count = 0

        for p in pos_list:
            p_margin = calculate_single_naked_margin(
                "BUY" if p["qty"] > 0 else "SELL",
                p["name"],
                abs(p["qty"]),
                p["avg_price"],
                p["lot_size"] or 25
            )
            sym_naked += p_margin

            is_long = p["qty"] > 0
            is_fut = "FUT" in p["name"]
            is_ce = p["name"].endswith("CE")
            is_pe = p["name"].endswith("PE")

            if is_fut and is_long: has_long_fut = True
            elif is_fut and not is_long: has_short_fut = True
            elif is_pe and is_long: long_pe_count += 1
            elif is_pe and not is_long: short_pe_count += 1
            elif is_ce and is_long: long_ce_count += 1
            elif is_ce and not is_long: short_ce_count += 1

        total_naked_margin += sym_naked

        # Check Hedge Cases:
        # Case 1: Married Put (Long Future + Long Put)
        if has_long_fut and long_pe_count > 0:
            # 60% margin benefit on Future leg
            discount = 70000.0 * min(1, long_pe_count)
            total_hedge_discount += discount

        # Case 2: Covered Call / Protective Call (Short Future + Long Call)
        elif has_short_fut and long_ce_count > 0:
            discount = 70000.0 * min(1, long_ce_count)
            total_hedge_discount += discount

        # Case 3: Option Spreads (Short Call + Long Call or Short Put + Long Put)
        if (short_ce_count > 0 and long_ce_count > 0) or (short_pe_count > 0 and long_pe_count > 0):
            discount = 65000.0
            total_hedge_discount += discount

    effective_margin = max(0.0, total_naked_margin - total_hedge_discount)

    return {
        "total_margin": round(effective_margin, 2),
        "naked_margin": round(total_naked_margin, 2),
        "hedge_benefit": round(total_hedge_discount, 2)
    }

# =========================================================================
# LEDGER & STATEMENT SYSTEM
# =========================================================================
def record_ledger_entry(user_id, particulars, voucher_type, debit=0.0, credit=0.0):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM ledger WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
    last_row = cursor.fetchone()
    current_bal = last_row["balance"] if last_row else 10000000.0

    new_balance = round(current_bal + credit - debit, 2)
    now_date = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")

    cursor.execute("""
        INSERT INTO ledger (user_id, date, time, particulars, voucher_type, debit, credit, balance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, now_date, now_time, particulars, voucher_type, debit, credit, new_balance))
    conn.commit()
    conn.close()
    return new_balance

def get_user_ledger(user_id=1):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ledger WHERE user_id = ? ORDER BY id DESC LIMIT 200", (user_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

# =========================================================================
# ORDER PLACEMENT WITH RMS CHECKS
# =========================================================================
def place_order(type_, name, token, product, qty, order_price, strategy_id="strat_general", stoploss=None, target=None, user_id=1):
    exec_price = float(order_price)
    lot_size = 30 if "BANKNIFTY" in name else (65 if "NIFTY" in name and not ("BANK" in name) else 500)

    conn = get_connection()
    cursor = conn.cursor()

    # 1. Check User RMS Lock
    cursor.execute("SELECT is_rms_locked, lock_reason, available_margin, used_margin, total_capital FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        conn.close()
        return {"success": False, "message": "User not found."}

    if user["is_rms_locked"]:
        conn.close()
        return {
            "success": False, 
            "message": f"🚨 ORDER REJECTED BY RMS: Account locked ({user['lock_reason'] or 'Daily loss limit reached'}). Trading is disabled for today."
        }

    # 2. Check Per-Trade RMS Risk Limit (Max 1% = ₹1,00,000)
    if stoploss and stoploss > 0:
        potential_loss = abs(exec_price - stoploss) * qty
        if potential_loss > MAX_TRADE_RISK:
            conn.close()
            return {
                "success": False,
                "message": f"🚨 RMS REJECTION: Stoploss risk of ₹{potential_loss:,.2f} exceeds max 1% per-trade risk limit (₹1,00,000.00). Reduce quantity or tighten stoploss."
            }

    # 3. Check Initial Margin Required
    initial_margin = calculate_single_naked_margin(type_, name, qty, exec_price, lot_size)
    if initial_margin > user["available_margin"]:
        conn.close()
        return {
            "success": False,
            "message": f"Insufficient margin! Required: ₹{initial_margin:,.2f}, Available: ₹{user['available_margin']:,.2f}"
        }

    trade_qty = qty if type_ == "BUY" else -qty
    order_id = f"ORD_{int(time.time() * 1000)}"
    now_time = datetime.now().strftime("%H:%M:%S")
    now_date = datetime.now().strftime("%Y-%m-%d")

    # Record Executed Order
    cursor.execute("""
        INSERT INTO orders (id, user_id, time, date, type, name, token, product, qty, order_price, exec_price, strategy_id, stoploss, target, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETE')
    """, (order_id, user_id, now_time, now_date, type_, name, str(token), product, qty, str(exec_price), exec_price, strategy_id, stoploss, target))

    # Check Existing Position
    cursor.execute("""
        SELECT * FROM positions 
        WHERE user_id = ? AND name = ? AND product = ? AND strategy_id = ?
    """, (user_id, name, product, strategy_id))
    existing = cursor.fetchone()

    symbol_tag = "NIFTY" if "NIFTY" in name and not ("BANK" in name) else ("BANKNIFTY" if "BANKNIFTY" in name else name.split("2")[0])

    if existing:
        old_qty = existing["qty"]
        new_qty = old_qty + trade_qty

        if new_qty == 0:
            # Position fully squared off
            final_pnl = (exec_price - existing["avg_price"]) * old_qty if old_qty > 0 else (existing["avg_price"] - exec_price) * abs(old_qty)
            cursor.execute("""
                INSERT INTO closed_trades (user_id, time, date, name, product, qty, buy_avg, exit_avg, pnl, strategy_id, exit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPPOSITE_ORDER')
            """, (user_id, now_time, now_date, name, product, abs(old_qty), existing["avg_price"], exec_price, final_pnl, strategy_id))

            cursor.execute("DELETE FROM positions WHERE id = ?", (existing["id"],))

            cursor.execute("""
                UPDATE users 
                SET realised_pnl = realised_pnl + ?
                WHERE id = ?
            """, (final_pnl, user_id))
            conn.commit()
            conn.close()

            # Record Ledger Entry
            if final_pnl >= 0:
                record_ledger_entry(user_id, f"Realised Profit: {name} x {abs(old_qty)}", "TRADE_PROFIT", debit=0.0, credit=final_pnl)
            else:
                record_ledger_entry(user_id, f"Realised Loss: {name} x {abs(old_qty)}", "TRADE_LOSS", debit=abs(final_pnl), credit=0.0)

            recalc_user_margins(user_id)
            return {"success": True, "message": f"{type_} {qty} Qty {name} placed successfully!", "order_id": order_id}
        else:
            # Adding / reducing position
            total_val = (abs(old_qty) * existing["avg_price"]) + (qty * exec_price)
            new_avg = round(total_val / abs(new_qty), 2)
            cursor.execute("""
                UPDATE positions 
                SET qty = ?, avg_price = ?, stoploss = ?, target = ?
                WHERE id = ?
            """, (new_qty, new_avg, stoploss, target, existing["id"]))
    else:
        # Create New Position
        pos_id = f"POS_{int(time.time() * 1000)}"
        cursor.execute("""
            INSERT INTO positions (id, user_id, token, symbol, name, product, qty, lot_size, avg_price, ltp, strategy_id, stoploss, target, used_margin, created_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pos_id, user_id, str(token), symbol_tag, name, product, trade_qty, lot_size, exec_price, exec_price, strategy_id, stoploss, target, initial_margin, now_date, now_time))

    conn.commit()
    conn.close()

    # Recalculate Portfolio Hedge Margin
    recalc_user_margins(user_id)

    return {"success": True, "message": f"{type_} {qty} Qty {name} placed successfully!", "order_id": order_id}

def recalc_user_margins(user_id=1):
    """Recomputes total portfolio margin with hedge benefits and updates user record"""
    hedge_data = calculate_portfolio_hedge_margin(user_id)
    eff_margin = hedge_data["total_margin"]

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT total_capital, realised_pnl FROM users WHERE id = ?", (user_id,))
    u = cursor.fetchone()
    if u:
        total_cap = u["total_capital"] + u["realised_pnl"]
        avail_margin = max(0.0, total_cap - eff_margin)
        cursor.execute("""
            UPDATE users 
            SET used_margin = ?, available_margin = ?
            WHERE id = ?
        """, (eff_margin, avail_margin, user_id))
        conn.commit()
    conn.close()

def exit_position(pos_id, exit_qty, exit_price, exit_reason="MANUAL", user_id=1):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM positions WHERE id = ? AND user_id = ?", (pos_id, user_id))
    pos = cursor.fetchone()

    if not pos:
        conn.close()
        return {"success": False, "message": "Position not found."}

    exit_qty = int(exit_qty)
    total_qty = abs(pos["qty"])
    is_long = pos["qty"] > 0
    exit_type = "SELL" if is_long else "BUY"
    now_time = datetime.now().strftime("%H:%M:%S")
    now_date = datetime.now().strftime("%Y-%m-%d")

    if exit_qty > total_qty:
        exit_qty = total_qty

    pnl = (exit_price - pos["avg_price"]) * exit_qty if is_long else (pos["avg_price"] - exit_price) * exit_qty

    # Record Closed Trade
    cursor.execute("""
        INSERT INTO closed_trades (user_id, time, date, name, product, qty, buy_avg, exit_avg, pnl, strategy_id, exit_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, now_time, now_date, pos["name"], pos["product"], exit_qty, pos["avg_price"], exit_price, pnl, pos["strategy_id"], exit_reason))

    # Record Order
    order_id = f"ORD_{int(time.time() * 1000)}"
    cursor.execute("""
        INSERT INTO orders (id, user_id, time, date, type, name, token, product, qty, order_price, exec_price, strategy_id, status, exit_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'MARKET', ?, ?, 'COMPLETE', ?)
    """, (order_id, user_id, now_time, now_date, exit_type, pos["name"], pos["token"], pos["product"], exit_qty, exit_price, pos["strategy_id"], exit_reason))

    if exit_qty == total_qty:
        cursor.execute("DELETE FROM positions WHERE id = ?", (pos_id,))
    else:
        remaining_qty = (total_qty - exit_qty) * (1 if is_long else -1)
        cursor.execute("UPDATE positions SET qty = ? WHERE id = ?", (remaining_qty, pos_id))

    cursor.execute("UPDATE users SET realised_pnl = realised_pnl + ? WHERE id = ?", (pnl, user_id))
    conn.commit()
    conn.close()

    # Record in Ledger after main commit
    if pnl >= 0:
        record_ledger_entry(user_id, f"Realised Profit: {pos['name']} x {exit_qty} Qty", "TRADE_PROFIT", debit=0.0, credit=pnl)
    else:
        record_ledger_entry(user_id, f"Realised Loss: {pos['name']} x {exit_qty} Qty", "TRADE_LOSS", debit=abs(pnl), credit=0.0)

    recalc_user_margins(user_id)
    return {"success": True, "message": f"Squared off {exit_qty} Qty of {pos['name']} @ ₹{exit_price:.2f} (P&L: ₹{pnl:+,.2f})", "pnl": pnl}

# =========================================================================
# RMS DAILY LOSS AUTO SQUARE-OFF MONITOR
# =========================================================================
def check_all_users_rms_and_sl(live_prices):
    """
    Called every second in background:
    1. Checks SL and Target triggers for all users
    2. Checks Daily 1% Loss Limit for all users -> auto square-off MIS if breached!
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, realised_pnl, is_rms_locked FROM users")
    users = [dict(r) for r in cursor.fetchall()]

    triggered_alerts = []

    for u in users:
        u_id = u["id"]
        cursor.execute("SELECT * FROM positions WHERE user_id = ?", (u_id,))
        positions = [dict(r) for r in cursor.fetchall()]

        unrealised = 0.0
        for p in positions:
            token = str(p["token"])
            ltp = float(live_prices.get(token, p["ltp"] or p["avg_price"]))
            is_long = p["qty"] > 0
            pnl = (ltp - p["avg_price"]) * p["qty"] if is_long else (p["avg_price"] - ltp) * abs(p["qty"])
            unrealised += pnl

            # Check SL / Target Hit
            sl = p.get("stoploss")
            tgt = p.get("target")
            hit_reason = None
            if is_long:
                if tgt and tgt > 0 and ltp >= tgt: hit_reason = "TARGET_HIT"
                elif sl and sl > 0 and ltp <= sl: hit_reason = "SL_HIT"
            else:
                if tgt and tgt > 0 and ltp <= tgt: hit_reason = "TARGET_HIT"
                elif sl and sl > 0 and ltp >= sl: hit_reason = "SL_HIT"

            if hit_reason:
                exit_position(p["id"], abs(p["qty"]), ltp, exit_reason=hit_reason, user_id=u_id)
                triggered_alerts.append({
                    "user": u["username"],
                    "instrument": p["name"],
                    "reason": hit_reason,
                    "price": ltp
                })

        # Check RMS Daily MTM Loss (-1% = -₹1,00,000)
        total_daily_pnl = u["realised_pnl"] + unrealised
        if total_daily_pnl <= -MAX_DAILY_LOSS and not u["is_rms_locked"]:
            # Trigger RMS Auto Square-off for all MIS positions
            print(f"[RMS ALERT] User {u['username']} breached daily 1% loss limit (MTM: ₹{total_daily_pnl:,.2f})! Square-off triggered.")
            for p in positions:
                if p["product"] == "MIS":
                    token = str(p["token"])
                    ltp = float(live_prices.get(token, p["ltp"] or p["avg_price"]))
                    exit_position(p["id"], abs(p["qty"]), ltp, exit_reason="RMS_DAILY_LOSS_LIMIT", user_id=u_id)

            cursor.execute("""
                UPDATE users 
                SET is_rms_locked = 1, lock_reason = 'Daily 1% loss limit breached (-₹1,00,000)'
                WHERE id = ?
            """, (u_id,))
            conn.commit()

            triggered_alerts.append({
                "user": u["username"],
                "reason": "RMS_AUTO_SQUARE_OFF",
                "message": f"Daily loss limit breached (-₹1,00,000). Account locked for today."
            })

    conn.close()
    return triggered_alerts

def get_grouped_positions(user_id=1, live_prices={}):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM strategies WHERE user_id = ? OR user_id = 1 ORDER BY name ASC", (user_id,))
    strategies = {s["id"]: dict(s) for s in cursor.fetchall()}

    cursor.execute("SELECT * FROM positions WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    positions = [dict(row) for row in cursor.fetchall()]
    conn.close()

    grouped = {}
    total_unrealised_pnl = 0.0

    for pos in positions:
        token = str(pos["token"])
        if token in live_prices:
            pos["ltp"] = float(live_prices[token])

        is_long = pos["qty"] > 0
        pnl = (pos["ltp"] - pos["avg_price"]) * pos["qty"] if is_long else (pos["avg_price"] - pos["ltp"]) * abs(pos["qty"])
        pos["pnl"] = round(pnl, 2)
        total_unrealised_pnl += pos["pnl"]

        strat_id = pos["strategy_id"] or "strat_general"
        if strat_id not in grouped:
            strat_meta = strategies.get(strat_id, {"name": "General Setup", "color": "#387ed1"})
            grouped[strat_id] = {
                "strategy_id": strat_id,
                "name": strat_meta["name"],
                "color": strat_meta["color"],
                "positions": [],
                "total_pnl": 0.0
            }

        grouped[strat_id]["positions"].append(pos)
        grouped[strat_id]["total_pnl"] += pos["pnl"]

    for g in grouped.values():
        g["total_pnl"] = round(g["total_pnl"], 2)

    hedge_info = calculate_portfolio_hedge_margin(user_id)
    acc = get_user_account(user_id)
    if acc:
        acc["unrealised_pnl"] = round(total_unrealised_pnl, 2)
        acc["net_worth"] = round(acc["total_capital"] + acc["realised_pnl"] + total_unrealised_pnl, 2)
        acc["hedge_benefit"] = hedge_info["hedge_benefit"]
        acc["naked_margin"] = hedge_info["naked_margin"]

    return {
        "groups": list(grouped.values()),
        "total_positions": len(positions),
        "account": acc,
        "hedge_info": hedge_info
    }

def get_desk_leaderboard(live_prices={}):
    """Returns Desk Monitor Leaderboard for all 18 office traders"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE role = 'trader' ORDER BY desk_no ASC")
    users = [dict(r) for r in cursor.fetchall()]

    leaderboard = []
    for u in users:
        u_id = u["id"]
        cursor.execute("SELECT * FROM positions WHERE user_id = ?", (u_id,))
        pos_list = [dict(p) for p in cursor.fetchall()]

        unrealised = 0.0
        for p in pos_list:
            token = str(p["token"])
            ltp = float(live_prices.get(token, p["ltp"] or p["avg_price"]))
            pnl = (ltp - p["avg_price"]) * p["qty"] if p["qty"] > 0 else (p["avg_price"] - ltp) * abs(p["qty"])
            unrealised += pnl

        net_pnl = round(u["realised_pnl"] + unrealised, 2)
        leaderboard.append({
            "desk_no": u["desk_no"],
            "username": u["username"],
            "display_name": u["display_name"],
            "positions_count": len(pos_list),
            "unrealised_pnl": round(unrealised, 2),
            "realised_pnl": round(u["realised_pnl"], 2),
            "net_pnl": net_pnl,
            "used_margin": u["used_margin"],
            "is_locked": u["is_rms_locked"],
            "lock_reason": u.get("lock_reason", "")
        })

    conn.close()
    return leaderboard

def reset_user_account(user_id=1):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM positions WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM orders WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM closed_trades WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM ledger WHERE user_id = ?", (user_id,))

    cursor.execute("""
        UPDATE users 
        SET total_capital = 10000000.0, available_margin = 10000000.0, 
            used_margin = 0.0, realised_pnl = 0.0, is_rms_locked = 0, lock_reason = NULL
        WHERE id = ?
    """, (user_id,))

    now_date = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")
    cursor.execute("""
        INSERT INTO ledger (user_id, date, time, particulars, voucher_type, debit, credit, balance)
        VALUES (?, ?, ?, 'Opening Capital Allocation (Evemeria Desk)', 'INITIAL_DEPOSIT', 0.0, 10000000.0, 10000000.0)
    """, (user_id, now_date, now_time))

    conn.commit()
    conn.close()
    return {"success": True, "message": "Account reset to ₹1,00,00,000 (1 Crore) with 0 positions."}

def exit_strategy_positions(strategy_id, user_id=1, live_prices={}):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM positions WHERE strategy_id = ? AND user_id = ?", (strategy_id, user_id))
    pos_list = [dict(p) for p in cursor.fetchall()]
    conn.close()

    count = 0
    for pos in pos_list:
        token = str(pos["token"])
        ltp = float(live_prices.get(token, pos["ltp"] or pos["avg_price"]))
        exit_position(pos["id"], abs(pos["qty"]), ltp, exit_reason="STRATEGY_EXIT", user_id=user_id)
        count += 1

    return {"success": True, "message": f"Squared off {count} positions under strategy."}

