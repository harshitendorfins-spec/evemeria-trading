@echo off
title Evemeria Virtual F^&O - Online Shareable Link
cd /d "%~dp0"

echo ===================================================================
echo   EVEMERIA VIRTUAL F^&O - LIVE MARKET TRADING TERMINAL
echo ===================================================================
echo.
echo 1. Checking Python Environment...
set PYTHON_CMD=python
set VENV_PYTHON="..\.venv\Scripts\python.exe"

if exist %VENV_PYTHON% (
    set PYTHON_CMD=%VENV_PYTHON%
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        set PYTHON_CMD=py
    )
)

echo 2. Starting Evemeria Trading Engine & API Server on Port 8080...
start "Evemeria Server" /min %PYTHON_CMD% app.py

timeout /t 3 /nobreak >nul

echo 3. Launching Cloudflare Tunnel for Live Public Link...
echo ===================================================================
echo NOTE: Below you will see a public https://....trycloudflare.com link.
echo Share that link with your traders or open on mobile phone!
echo ===================================================================
echo.

if exist ".\cloudflared.exe" (
    .\cloudflared.exe tunnel --url http://127.0.0.1:8080
) else (
    echo [ERROR] cloudflared.exe not found in current folder!
    pause
)

pause
