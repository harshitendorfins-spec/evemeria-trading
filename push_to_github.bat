@echo off
title Push Evemeria to GitHub
cd /d "%~dp0"

echo ===================================================================
echo             PUSH EVEMERIA PLATFORM TO GITHUB
echo ===================================================================
echo.
echo Target Repository: https://github.com/harshitendorfins-spec/evemeria-trading.git
echo.
echo GitHub requires a Personal Access Token (PAT) for uploading code.
echo (Get one instantly here: https://github.com/settings/tokens/new)
echo.

set /p GITHUB_TOKEN="Paste your GitHub Token (starts with ghp_...): "

if "%GITHUB_TOKEN%"=="" (
    echo [ERROR] Token cannot be empty!
    pause
    exit /b
)

set GIT_EXE="..\mingit\cmd\git.exe"

echo.
echo Pushing code to GitHub...
%GIT_EXE% push "https://%GITHUB_TOKEN%@github.com/harshitendorfins-spec/evemeria-trading.git" main -u

if %errorlevel% equ 0 (
    echo.
    echo ===================================================================
    echo [SUCCESS] Evemeria platform successfully uploaded to GitHub!
    echo Check it at: https://github.com/harshitendorfins-spec/evemeria-trading
    echo ===================================================================
) else (
    echo.
    echo [ERROR] Push failed. Please check that your token has 'repo' permission enabled.
)

echo.
pause
