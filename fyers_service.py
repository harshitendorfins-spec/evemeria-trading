import os
import json
import time
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from database import get_connection

IST = timezone(timedelta(hours=5, minutes=30))

class FyersService:
    def __init__(self):
        self.app_id = ""
        self.access_token = ""
        self.secret_key = ""
        self.redirect_uri = "https://trade.fyers.in/api-login/redirect-uri/index.html"
        self.is_authenticated = False
        self.user_profile = None
        self.live_prices = {}  # { "token": ltp, "symbol": ltp }
        self.last_sync_time = None
        self.load_saved_credentials()

    def load_saved_credentials(self):
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT app_id, access_token, secret_key, redirect_uri, is_connected FROM fyers_settings WHERE id = 1")
            row = cursor.fetchone()
            conn.close()

            if row:
                self.app_id = row["app_id"] or ""
                self.access_token = row["access_token"] or ""
                self.secret_key = row["secret_key"] or ""
                self.redirect_uri = row["redirect_uri"] or "https://trade.fyers.in/api-login/redirect-uri/index.html"
                if self.app_id and self.access_token:
                    # Don't auto-assume true until verified, or restore state
                    self.is_authenticated = bool(row["is_connected"])
        except Exception as e:
            print(f"[FyersService DB Warning] {e}")

    def save_credentials(self, app_id, access_token, secret_key="", redirect_uri=""):
        self.app_id = (app_id or "").strip()
        self.access_token = (access_token or "").strip()
        if secret_key:
            self.secret_key = secret_key.strip()
        if redirect_uri:
            self.redirect_uri = redirect_uri.strip()

        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE fyers_settings 
                SET app_id = ?, access_token = ?, secret_key = ?, redirect_uri = ?, is_connected = ?, last_connected = ?
                WHERE id = 1
            """, (self.app_id, self.access_token, self.secret_key, self.redirect_uri, 1 if self.is_authenticated else 0, datetime.now(IST).isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[FyersService Save DB Error] {e}")

    def generate_auth_url(self, app_id=None, secret_key=None, redirect_uri=None):
        """Generates the Fyers v3 OAuth login URL to obtain an auth_code"""
        if app_id:
            self.app_id = app_id.strip()
        if secret_key:
            self.secret_key = secret_key.strip()
        if redirect_uri:
            self.redirect_uri = redirect_uri.strip()

        if not self.app_id:
            return {"success": False, "message": "App ID (Client ID) is required."}

        url = (
            f"https://api-t1.fyers.in/api/v3/generate-authcode?"
            f"client_id={self.app_id}&"
            f"redirect_uri={requests.utils.quote(self.redirect_uri)}&"
            f"response_type=code&"
            f"state=evemeria_trading"
        )
        return {
            "success": True,
            "auth_url": url,
            "redirect_uri": self.redirect_uri,
            "instructions": "1. Click the link to open Fyers login.\n2. Complete 2FA login.\n3. Copy the 'auth_code' parameter from the redirect address bar and paste it below."
        }

    def exchange_auth_code(self, app_id, secret_key, auth_code):
        """Exchanges an auth_code for a 24-hr Fyers API v3 access_token using SHA-256 appIdHash"""
        app_id_clean = app_id.strip()
        secret_clean = secret_key.strip()
        code_clean = auth_code.strip()

        if not app_id_clean or not secret_clean or not code_clean:
            return {"success": False, "message": "App ID, Secret Key, and Auth Code are all required."}

        # Calculate SHA256 of client_id:secret_key
        hash_input = f"{app_id_clean}:{secret_clean}"
        app_id_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

        url = "https://api-t1.fyers.in/api/v3/validate-authcode"
        payload = {
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash,
            "code": code_clean
        }
        headers = {"Content-Type": "application/json"}

        try:
            res = requests.post(url, json=payload, headers=headers, timeout=12)
            data = res.json()
            if res.status_code == 200 and data.get("s") == "ok" and data.get("access_token"):
                token = data["access_token"]
                return self.connect(app_id=app_id_clean, access_token=token, secret_key=secret_clean)
            else:
                msg = data.get("message") or str(data)
                return {"success": False, "message": f"Fyers Token Generation Failed: {msg}"}
        except Exception as e:
            return {"success": False, "message": f"Connection error during token exchange: {str(e)}"}

    def connect(self, app_id=None, access_token=None, secret_key=None, auth_code=None, redirect_uri=None):
        """Authenticates Fyers API v3 using App ID and Access Token (or exchanges Auth Code)"""
        # If user supplied auth_code + secret_key, exchange first
        if auth_code and secret_key and app_id and not access_token:
            return self.exchange_auth_code(app_id, secret_key, auth_code)

        if app_id:
            self.app_id = app_id.strip()
        if access_token:
            self.access_token = access_token.strip()
        if secret_key:
            self.secret_key = secret_key.strip()
        if redirect_uri:
            self.redirect_uri = redirect_uri.strip()

        if not self.app_id or not self.access_token:
            return {
                "success": False,
                "message": "Both App ID (e.g. XXXXXX-100) and Access Token are required to connect Fyers API."
            }

        # Verify credentials by testing profile endpoint
        headers = {
            "Authorization": f"{self.app_id}:{self.access_token}",
            "Content-Type": "application/json"
        }

        try:
            # 1. Test Profile Endpoint
            prof_url = "https://api-t1.fyers.in/api/v3/profile"
            res = requests.get(prof_url, headers=headers, timeout=10)
            data = res.json()

            if res.status_code == 200 and (data.get("s") == "ok" or data.get("code") == 200):
                self.is_authenticated = True
                self.user_profile = data.get("data", {})
                self.save_credentials(self.app_id, self.access_token, self.secret_key, self.redirect_uri)
                client_name = self.user_profile.get("name", self.app_id)
                return {
                    "success": True,
                    "message": f"Fyers API connected successfully for {client_name}! 🚀",
                    "profile": self.user_profile
                }

            # 2. Fallback check: Test Quotes Endpoint
            q_url = "https://api-t1.fyers.in/data/quotes?symbols=NSE:NIFTY50-INDEX"
            q_res = requests.get(q_url, headers=headers, timeout=10)
            q_data = q_res.json()
            if q_res.status_code == 200 and q_data.get("s") == "ok":
                self.is_authenticated = True
                self.save_credentials(self.app_id, self.access_token, self.secret_key, self.redirect_uri)
                return {
                    "success": True,
                    "message": "Fyers API connected successfully! Live market feed is active.",
                    "profile": {"name": self.app_id}
                }

            err_msg = data.get("message") or q_data.get("message") or "Authentication failed. Check your App ID and Access Token."
            self.is_authenticated = False
            return {"success": False, "message": f"Fyers Error: {err_msg}"}

        except Exception as e:
            self.is_authenticated = False
            return {"success": False, "message": f"Fyers Connection Error: {str(e)}"}

    def get_quotes(self, symbols: list):
        """Fetch quotes for up to 50 symbols from Fyers API"""
        if not self.is_authenticated or not self.app_id or not self.access_token:
            return None

        if not symbols:
            return None

        sym_str = ",".join(symbols[:50])
        url = f"https://api-t1.fyers.in/data/quotes?symbols={sym_str}"
        headers = {
            "Authorization": f"{self.app_id}:{self.access_token}"
        }

        try:
            res = requests.get(url, headers=headers, timeout=6)
            if res.status_code == 200:
                data = res.json()
                if data.get("s") == "ok" and "d" in data:
                    out = {}
                    for item in data["d"]:
                        v = item.get("v", {})
                        sym = v.get("symbol") or item.get("n")
                        if sym and "lp" in v:
                            ltp = float(v.get("lp", 0.0))
                            prev_close = float(v.get("prev_close_price", ltp))
                            ch = float(v.get("ch", round(ltp - prev_close, 2)))
                            chp = float(v.get("chp", 0.0))
                            out[sym] = {
                                "ltp": ltp,
                                "close": prev_close,
                                "chg": ch,
                                "chg_pct": chp,
                                "high": float(v.get("high_price", ltp)),
                                "low": float(v.get("low_price", ltp)),
                                "open": float(v.get("open_price", ltp)),
                            }
                            # Cache in live_prices
                            self.live_prices[sym] = ltp
                    return out
        except Exception as e:
            print(f"[Fyers Quotes Error] {e}")
        return None

    def get_market_status(self):
        """Check if Indian market is currently OPEN (9:15 AM to 3:30 PM Mon-Fri IST)"""
        now_ist = datetime.now(IST)
        is_weekday = now_ist.weekday() < 5  # Mon = 0, Fri = 4
        mins = now_ist.hour * 60 + now_ist.minute
        # 09:15 AM = 555 mins, 15:30 (3:30 PM) = 930 mins
        is_open = is_weekday and (555 <= mins <= 930)

        status_text = "OPEN" if is_open else "CLOSED"
        message = "Live Market Session Active" if is_open else "Market Closed (Trading Hours: 09:15 AM - 03:30 PM IST, Mon-Fri)"

        return {
            "is_open": is_open,
            "status": status_text,
            "message": message,
            "current_time": now_ist.strftime("%I:%M:%S %p IST"),
            "current_date": now_ist.strftime("%Y-%m-%d"),
            "is_connected": self.is_authenticated,
            "broker": "Fyers",
            "app_id": self.app_id if self.is_authenticated else ""
        }

fyers_service = FyersService()
