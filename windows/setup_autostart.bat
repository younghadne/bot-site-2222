@echo off
title Instagram Bot - Setup Auto-Start
color 0B

echo ============================================
echo   Instagram Bot - Auto-Start Setup
echo   Run this ONCE as Administrator
echo ============================================
echo.

:: ── Check admin rights ───────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Please right-click this file and choose
    echo        "Run as Administrator"
    pause
    exit /b 1
)

:: ── Copy bot files ───────────────────────────
echo [1/5] Creating bot directory...
if not exist "C:\instagram-bot" mkdir "C:\instagram-bot"

echo [2/5] Copying bot files...
xcopy /e /y /i "%~dp0..\*" "C:\instagram-bot\" /exclude:"%~dp0exclude.txt" >nul 2>&1
xcopy /e /y /i "%~dp0..\templates" "C:\instagram-bot\templates\" >nul 2>&1
copy /y "%~dp0..\web_app.py" "C:\instagram-bot\" >nul
copy /y "%~dp0..\requirements.txt" "C:\instagram-bot\" >nul
copy /y "%~dp0start_bot.bat" "C:\instagram-bot\" >nul
echo    Done.

:: ── Install Python dependencies ──────────────
echo [3/5] Installing Python packages...
cd /d "C:\instagram-bot"
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is installed
    echo        and "Add Python to PATH" was checked during install.
    pause
    exit /b 1
)
echo    Done.

:: ── Register Task Scheduler ──────────────────
echo [4/5] Registering auto-start task...
schtasks /delete /tn "InstagramBot" /f >nul 2>&1
schtasks /create ^
  /tn "InstagramBot" ^
  /tr "C:\instagram-bot\start_bot.bat" ^
  /sc ONSTART ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /delay 0000:15 ^
  /f

if %errorlevel% equ 0 (
    echo    Task registered successfully!
) else (
    echo    Warning: Task registration failed. You can still run
    echo    start_bot.bat manually.
)

:: ── Create desktop shortcut ──────────────────
echo [5/5] Creating desktop shortcut...
powershell -command "$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Instagram Bot.lnk'); $s.TargetPath='C:\instagram-bot\start_bot.bat'; $s.WorkingDirectory='C:\instagram-bot'; $s.Description='Start Instagram Bot'; $s.Save()"
echo    Shortcut created on Desktop.

echo.
echo ============================================
echo   SETUP COMPLETE!
echo.
echo   The bot will now start automatically
echo   every time Windows boots.
echo.
echo   You can also double-click:
echo   "Instagram Bot" on your Desktop
echo.
echo   Next steps:
echo   1. Make sure ngrok is at C:\ngrok\ngrok.exe
echo   2. Run: C:\ngrok\ngrok.exe authtoken YOUR_TOKEN
echo   3. Reboot or double-click the Desktop shortcut
echo ============================================
echo.
pause
