import os
import json
import time
import requests
import pyotp
from datetime import datetime
from SmartApi import SmartConnect
from database import get_connection

class AngelOneService:
    def __init__(self):
        self.smart_api = None
        self.auth_token = None
        self.feed_token = None
        self.refresh_token = None
        self.is_authenticated = False
        self.user_profile = None
        self.token_cache = {}
        self.live_prices = {} # { "token": ltp }
        self.last_sync_time = None
        self.load_saved_credentials()

    def load_saved_credentials(self):
        self.api_key = ""
        self.client_id = ""
        self.pin = ""
        self.totp_secret = ""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT api_key, client_id, pin, totp_secret FROM angel_settings WHERE id = 1")
            row = cursor.fetchone()
            conn.close()

            if row and row["api_key"] and row["client_id"]:
                self.api_key = row["api_key"]
                self.client_id = row["client_id"]
                self.pin = row["pin"]
                self.totp_secret = row["totp_secret"]
        except Exception as e:
            print(f"[AngelService DB Warning] Could not load credentials yet: {e}")

    def save_credentials(self, api_key, client_id, pin, totp_secret):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE angel_settings 
            SET api_key = ?, client_id = ?, pin = ?, totp_secret = ?
            WHERE id = 1
        """, (api_key, client_id, pin, totp_secret))
        conn.commit()
        conn.close()
        self.api_key = api_key
        self.client_id = client_id
        self.pin = pin
        self.totp_secret = totp_secret

    def connect(self, api_key=None, client_id=None, pin=None, totp_secret=None):
        if api_key:
            self.save_credentials(api_key, client_id, pin, totp_secret)
        else:
            self.load_saved_credentials()

        if not self.api_key or not self.client_id or not self.pin or not self.totp_secret:
            return {"success": False, "message": "API Key, Client ID, PIN and TOTP Secret are required."}

        try:
            self.smart_api = SmartConnect(api_key=self.api_key.strip())
            clean_totp = str(self.totp_secret).replace(" ", "").strip().upper()
            
            # Smart check: if user entered 6-digit code directly from Authenticator app
            if clean_totp.isdigit() and len(clean_totp) == 6:
                totp = clean_totp
            else:
                try:
                    totp = pyotp.TOTP(clean_totp).now()
                except Exception:
                    totp = clean_totp
            
            print(f"[AngelOne] Attempting login for client: {self.client_id} with TOTP: {totp}")
            data = self.smart_api.generateSession(self.client_id.strip(), str(self.pin).strip(), totp)
            print(f"[AngelOne Response] {data}")

            if data and data.get("status"):
                self.auth_token = data["data"]["jwtToken"]
                self.refresh_token = data["data"]["refreshToken"]
                self.feed_token = data["data"]["feedToken"]
                self.is_authenticated = True
                self.user_profile = data["data"]
                self.last_sync_time = datetime.now().isoformat()

                # Update in database
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE angel_settings 
                    SET feed_token = ?, is_connected = 1, last_connected = ?
                    WHERE id = 1
                """, (self.feed_token, self.last_sync_time))
                conn.commit()
                conn.close()

                return {
                    "success": True, 
                    "message": "Successfully connected to Angel One SmartAPI!",
                    "client_id": self.client_id,
                    "name": data["data"].get("name", self.client_id)
                }
            else:
                self.is_authenticated = False
                error_msg = data.get("message", "Authentication failed. Check your Client ID, PIN, or TOTP.")
                error_code = data.get("errorcode", "")
                return {"success": False, "message": f"{error_msg} (Code: {error_code})"}

        except Exception as e:
            self.is_authenticated = False
            print(f"[AngelOne Exception] {e}")
            return {"success": False, "message": f"Login error: {str(e)}"}

    def get_ltp(self, exchange, symbol, token):
        """Fetch live LTP from Angel One REST API for a specific instrument"""
        if not self.is_authenticated or not self.smart_api:
            return None

        try:
            res = self.smart_api.ltpData(exchange=exchange, tradingsymbol=symbol, symboltoken=str(token))
            if res and res.get("status") and res.get("data"):
                ltp = float(res["data"].get("ltp", 0.0))
                self.live_prices[str(token)] = ltp
                return ltp
        except Exception as e:
            print(f"[AngelOne Error] get_ltp {symbol}: {e}")
        return None

    def get_market_status(self):
        """Check if Indian market is currently OPEN (9:15 AM to 3:30 PM Mon-Fri)"""
        now = datetime.now()
        is_weekday = now.weekday() < 5 # Mon = 0, Fri = 4
        current_time = now.time()
        market_open = datetime.strptime("09:15:00", "%H:%M:%S").time()
        market_close = datetime.strptime("15:30:00", "%H:%M:%S").time()

        is_open = is_weekday and (market_open <= current_time <= market_close)
        
        status_text = "OPEN" if is_open else "CLOSED"
        message = "Live Market Session Active" if is_open else "Market Closed (Trading Hours: 09:15 AM - 03:30 PM, Mon-Fri)"

        return {
            "is_open": is_open,
            "status": status_text,
            "message": message,
            "current_time": now.strftime("%H:%M:%S"),
            "current_date": now.strftime("%Y-%m-%d"),
            "is_connected": self.is_authenticated
        }

angel_service = AngelOneService()
