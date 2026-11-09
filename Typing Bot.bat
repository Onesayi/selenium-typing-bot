@echo off
rem Double-click this file to run the typing bot on any typing site.
cd /d "%~dp0"

where python >nul 2>nul || (
    echo Python isn't installed or isn't on PATH. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)
python -c "import selenium" 2>nul || (
    echo Installing Selenium...
    python -m pip install selenium || ( pause & exit /b 1 )
)

set "URL="
set /p URL=Typing test address (URL):
set "WPM=85"
set /p WPM=Target speed in WPM [85]:
set "ACC=85"
set /p ACC=Target accuracy in %% [85]:
set "SECS="
set /p SECS=Stop after how many seconds? (blank = when the text runs out):

set "EXTRA="
if defined SECS set "EXTRA=--max-seconds %SECS%"
python typing_bot.py --url "%URL%" --wpm %WPM% --accuracy %ACC% %EXTRA%
echo.
pause
