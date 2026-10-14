@echo off
title Evemeria Virtual F^&O Platform (Fyers API Live)
cd /d "%~dp0"

echo ========================================================
echo Starting Evemeria Virtual F^&O Platform (18 Desks LAN)...
echo Accessible on Desktop and Mobile: http://192.168.10.42:8080
echo ========================================================

set VENV_PYTHON="..\.venv\Scripts\python.exe"

if exist %VENV_PYTHON% (
    start "" "http://127.0.0.1:8080"
    %VENV_PYTHON% app.py
) else (
    echo Virtual environment not found, launching with python...
    start "" "http://127.0.0.1:8080"
    python app.py
)

pause
