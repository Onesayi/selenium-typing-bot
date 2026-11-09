@echo off
rem Double-click this file to run the typing bot on any typing site.
cd /d "%~dp0"

where python >nul 2>nul || (
    echo Python isn't installed or isn't on PATH. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

set "ENGINE=1"
set /p ENGINE=Use [1] Selenium or [2] Playwright? [1]:
if "%ENGINE%"=="2" (
    set "BOT=typing_bot_pw.py"
    set "PKG=playwright"
) else (
    set "BOT=typing_bot.py"
    set "PKG=selenium"
)
python -c "import %PKG%" 2>nul || (
    echo Installing %PKG%...
    python -m pip install %PKG% || ( pause & exit /b 1 )
)

:ask_url
set "URL="
set /p URL=Typing test address (URL):
if not defined URL (
    echo Please paste the address of the typing test page.
    goto ask_url
)
set "WPM=85"
set /p WPM=Target speed in WPM [85]:
set "ACC=85"
set /p ACC=Target accuracy in %% [85]:
set "SECS="
set /p SECS=Stop after how many seconds? (blank = when the text runs out):

set "EXTRA="
if defined SECS set "EXTRA=--max-seconds %SECS%"
python %BOT% --url "%URL%" --wpm %WPM% --accuracy %ACC% %EXTRA%
echo.
pause
