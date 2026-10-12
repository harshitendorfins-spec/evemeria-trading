@echo off
title Allow Port 8080 in Windows Firewall
cd /d "%~dp0"

echo ========================================================
echo Adding Windows Firewall Inbound Rule for Port 8080...
echo ========================================================

netsh advfirewall firewall add rule name="Evemeria Virtual FO (Port 8080)" dir=in action=allow protocol=TCP localport=8080

if %errorlevel% neq 0 (
    echo Requesting Administrator permission... Click YES on the prompt.
    powershell -Command "Start-Process cmd -ArgumentList '/c netsh advfirewall firewall add rule name=\"Evemeria Virtual FO (Port 8080)\" dir=in action=allow protocol=TCP localport=8080' -Verb RunAs"
) else (
    echo Successfully opened Port 8080 in Windows Firewall!
)

echo Done.
