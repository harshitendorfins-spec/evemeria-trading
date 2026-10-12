import os
import json
from datetime import datetime

SCRIPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nfo_scrips.json")

def load_scrips():
    if os.path.exists(SCRIPS_FILE):
        with open(SCRIPS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def get_default_watchlist_contracts(nifty_spot=22368.0, bn_spot=54842.0):
    scrips = load_scrips()
    if not scrips:
        return []

    # Filter upcoming expiries
    expiries = sorted(list(set(s["expiry"] for s in scrips if s.get("expiry"))))
    
    # Target closest weekly expiry
    target_expiry = "13OCT2026"
    if target_expiry not in expiries and expiries:
        target_expiry = expiries[0]

    # NIFTY Strikes (around 22350: 22250, 22300, 22350, 22400, 22450)
    n_atm = round(nifty_spot / 50) * 50
    n_strikes = [n_atm - 100, n_atm - 50, n_atm, n_atm + 50, n_atm + 100]

    # Sample Stock F&O: RELIANCE Future, INFY Future, HDFCBANK Future
    stock_targets = ["RELIANCE", "INFY", "HDFCBANK", "SBIN"]

    watchlist = []

    # 1. Add Nifty Index Options
    for s in scrips:
        if s.get("expiry") == target_expiry and s.get("name") == "NIFTY":
            strike_val = s.get("strike", 0.0)
            if strike_val in n_strikes:
                is_ce = s.get("symbol", "").endswith("CE")
                watchlist.append({
                    "id": f"NFO_{s['token']}",
                    "token": s["token"],
                    "exchange": "NFO",
                    "symbol": "NIFTY",
                    "name": s["symbol"],
                    "type": "CE" if is_ce else "PE",
                    "strike": strike_val,
                    "lot": s.get("lotsize", 65),
                    "ltp": 0.0,
                    "close": 0.0,
                    "chg": 0.0,
                    "chg_pct": 0.0
                })

    # 2. Add Popular Stock Futures & Options
    monthly_expiry = "27OCT2026"
    for s in scrips:
        if s.get("expiry") == monthly_expiry and s.get("name") in stock_targets:
            if s.get("instrumenttype") == "FUTSTK":
                watchlist.append({
                    "id": f"NFO_{s['token']}",
                    "token": s["token"],
                    "exchange": "NFO",
                    "symbol": s["name"],
                    "name": s["symbol"],
                    "type": "FUT",
                    "strike": 0.0,
                    "lot": s.get("lotsize", 1),
                    "ltp": 0.0,
                    "close": 0.0,
                    "chg": 0.0,
                    "chg_pct": 0.0
                })

    return watchlist

def search_scrip_tokens(query):
    scrips = load_scrips()
    if not scrips:
        return []

    # Split user query into keywords: e.g. "RELIANCE FUT" or "INFY 1500 CE"
    terms = query.strip().upper().split()
    results = []

    for s in scrips:
        sym = s.get("symbol", "").upper()
        name = s.get("name", "").upper()
        inst = s.get("instrumenttype", "").upper()

        # Check if all terms match symbol, name or instrument type
        match_all = True
        for t in terms:
            if t == "FUT":
                if "FUT" not in sym and "FUT" not in inst:
                    match_all = False
                    break
            elif t == "CE":
                if not sym.endswith("CE"):
                    match_all = False
                    break
            elif t == "PE":
                if not sym.endswith("PE"):
                    match_all = False
                    break
            elif t not in sym and t not in name:
                match_all = False
                break

        if match_all:
            is_fut = "FUT" in inst or "FUT" in sym
            is_ce = sym.endswith("CE")
            inst_type = "FUT" if is_fut else ("CE" if is_ce else "PE")

            results.append({
                "id": f"NFO_{s['token']}",
                "token": s["token"],
                "exchange": "NFO",
                "symbol": s.get("name"),
                "name": s.get("symbol"),
                "type": inst_type,
                "strike": s.get("strike", 0.0),
                "lot": s.get("lotsize", 1),
                "expiry": s.get("expiry"),
                "instrumenttype": s.get("instrumenttype")
            })

            if len(results) >= 25:
                break

    # Prioritize Futures and shorter symbols
    results.sort(key=lambda x: (0 if x["type"] == "FUT" else 1, len(x["name"])))
    return results

if __name__ == "__main__":
    test_rel = search_scrip_tokens("RELIANCE FUT")
    print("RELIANCE FUT matches:", len(test_rel), test_rel[:2])
    test_infy = search_scrip_tokens("INFY CE")
    print("INFY CE matches:", len(test_infy), test_infy[:2])
