@echo off
title Push Evemeria to GitHub
cd /d "%~dp0"

echo ===================================================================
echo             PUSH EVEMERIA PLATFORM TO GITHUB
echo ===================================================================
echo.
echo Make sure you have created an empty repository on GitHub:
echo https://github.com/new
echo.

set /p REPO_URL="Enter your GitHub Repository URL (e.g., https://github.com/username/repo.git): "

if "%REPO_URL%"=="" (
    echo [ERROR] Repository URL cannot be empty!
    pause
    exit /b
)

set GIT_EXE="..\mingit\cmd\git.exe"

echo.
echo [1/3] Setting remote origin...
%GIT_EXE% remote remove origin >nul 2>&1
%GIT_EXE% remote add origin %REPO_URL%

echo.
echo [2/3] Verifying branch...
%GIT_EXE% branch -M main

echo.
echo [3/3] Pushing to GitHub...
%GIT_EXE% push -u origin main

if %errorlevel% equ 0 (
    echo.
    echo ===================================================================
    echo [SUCCESS] Code successfully pushed to GitHub!
    echo ===================================================================
) else (
    echo.
    echo [NOTE] If GitHub asked for authentication, use your GitHub Personal Access Token (PAT) as password.
)

echo.
pause
