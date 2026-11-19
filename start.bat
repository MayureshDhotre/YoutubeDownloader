@echo off
REM Launches the YouTube Downloader GUI on Windows.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set PYCMD=py -3
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set PYCMD=python
    ) else (
        echo Python 3 not found. Install it from https://www.python.org/downloads/
        echo and tick "Add Python to PATH" during setup.
        pause
        exit /b 1
    )
)

%PYCMD% yt_downloader.py %*
if %errorlevel% neq 0 pause
