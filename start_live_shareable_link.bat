@echo off
title Evemeria Virtual F^&O - Online Shareable Link
cd /d "%~dp0"

echo ===================================================================
echo   EVEMERIA VIRTUAL F^&O - LIVE MARKET TRADING TERMINAL
echo ===================================================================
echo.
echo 1. Checking / Starting Evemeria Server...
set VENV_PYTHON="..\.venv\Scripts\python.exe"

start "Evemeria Server" /min %VENV_PYTHON% app.py

timeout /t 3 /nobreak >nul

echo 2. Launching Cloudflare Tunnel for Live Public Link...
echo.
echo Connect your phone or share this link with your 18 traders!
echo ===================================================================
.\cloudflared.exe tunnel --url http://127.0.0.1:8080

pause
