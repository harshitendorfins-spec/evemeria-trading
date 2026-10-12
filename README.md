# Evemeria Virtual F&O Trading Platform 🚀

A multi-user virtual/paper trading terminal for **Futures & Options (F&O)** with live tick data from **Angel One SmartAPI**, realistic SPAN/hedge margin calculation, RMS risk engine, individual financial ledgers, and 18 isolated trader desks with master admin supervision.

---

## ✨ Features

1. **18 Isolated Trader Desks & Master Supervisor:**
   - **Trader View (`TRADER01` – `TRADER18`):** Each trader logs in with their PIN (`1234`) and accesses only their own private desk. Desk switching, monitor buttons, and capital manipulation are hidden from normal traders.
   - **Master Admin (`ADMIN`, PIN: `9999`):** Full supervisory portal displaying all 18 desks' live MTM matrix, total office net P&L, per-trader financial ledger audit statements, account reset, and desk unlock controls.

2. **Capital & RMS Risk Management:**
   - Fixed **₹1,00,00,000 (1 Crore)** capital per desk.
   - **Per-trade Risk Limit:** Max 1% (₹1,00,000) SL risk per order.
   - **Daily Loss Auto Square-off:** If a trader's intraday loss reaches 1% (₹1,00,000), the RMS engine automatically liquidates all open positions and locks the desk until unlocked by Admin.

3. **Intelligent Hedge Margin Benefit:**
   - Realistic margin reduction when holding hedged positions (e.g. Long Future + Long Put or Spreads).
   - Dynamic real-time calculation comparing naked margin vs effective hedged margin.

4. **Zerodha Kite Aesthetic UI:**
   - Full dark/light terminal with quick search, watchlist management, live market tick indicators, and order entry modals.
   - Full mobile and desktop responsiveness.

5. **Financial Ledger & Backoffice:**
   - Complete audit trail of bookings, credits, debits, and running balance for every desk.
   - CSV export for reporting.

---

## 🚀 Quick Start

### 1. Install Requirements
```bash
pip install -r requirements.txt
```

### 2. Run Local Server
```bash
python app.py
```
Open [http://localhost:8080](http://localhost:8080) in your browser.

### 3. Share Across Devices (Cloudflare Tunnel)
Double-click `start_live_shareable_link.bat` to launch the server and generate a secure public HTTPS link for all 18 traders.

---

## 🔒 Security
- All sensitive credentials (API keys, client IDs, TOTP secrets) and SQLite databases are excluded via `.gitignore`.
- SQLite database runs in WAL mode with connection pooling.
