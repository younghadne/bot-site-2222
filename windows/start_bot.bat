@echo off
title Instagram Bot Launcher
color 0A

echo ============================================
echo   Instagram Bot - Auto Startup
echo ============================================
echo.

:: ── Config ──────────────────────────────────
:: Change these two lines to match your setup
set BOT_DIR=C:\instagram-bot
set NGROK_PATH=C:\ngrok\ngrok.exe

:: ── Wait for network ─────────────────────────
echo [1/4] Waiting for network...
timeout /t 10 /nobreak >nul

:: ── Kill any old instances ───────────────────
echo [2/4] Cleaning up old processes...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im ngrok.exe  >nul 2>&1
timeout /t 2 /nobreak >nul

:: ── Start the bot ────────────────────────────
echo [3/4] Starting Instagram Bot...
cd /d "%BOT_DIR%"
start "Instagram Bot" /min cmd /c "python web_app.py >> C:\instagram-bot\bot.log 2>&1"
timeout /t 5 /nobreak >nul

:: ── Start ngrok ──────────────────────────────
echo [4/4] Starting ngrok tunnel...
start "ngrok" /min cmd /c "%NGROK_PATH% http 10000 --log=stdout >> C:\instagram-bot\ngrok.log 2>&1"
timeout /t 4 /nobreak >nul

:: ── Get the public ngrok URL ─────────────────
echo.
echo ============================================
echo   Getting your public URL...
echo ============================================
timeout /t 3 /nobreak >nul

:: Fetch ngrok public URL from its local API
curl -s http://localhost:4040/api/tunnels > C:\instagram-bot\ngrok_info.json 2>nul
findstr /i "public_url" C:\instagram-bot\ngrok_info.json >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo Your bot is live at:
    curl -s http://localhost:4040/api/tunnels | python -c "import sys,json; t=json.load(sys.stdin)['tunnels']; print(next((x['public_url'] for x in t if 'https' in x['public_url']),t[0]['public_url'] if t else 'URL not found'))" 2>nul
) else (
    echo.
    echo URL not ready yet - check http://localhost:4040 in your browser
    echo or wait 10 seconds and run: curl http://localhost:4040/api/tunnels
)

echo.
echo ============================================
echo   Bot is running in the background!
echo   Logs: C:\instagram-bot\bot.log
echo   Ngrok dashboard: http://localhost:4040
echo ============================================
echo.
pause
