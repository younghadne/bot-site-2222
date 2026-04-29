@echo off
title Instagram Bot - Stop
color 0C

echo ============================================
echo   Stopping Instagram Bot + ngrok...
echo ============================================
echo.

taskkill /f /im python.exe >nul 2>&1
taskkill /f /im ngrok.exe  >nul 2>&1

echo   Done. All processes stopped.
echo.
pause
