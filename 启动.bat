@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.9+ and tick "Add Python to PATH".
    pause
    exit /b 1
)

python -c "import numpy, pyaudiowpatch, PyQt5" >nul 2>&1
if errorlevel 1 (
    echo [1/2] Installing dependencies, please wait...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Install failed. Run: python -m pip install -r requirements.txt
        pause
        exit /b 1
    )
)

echo Starting FPS Audio Radar...
start "" pythonw fps_audio_radar.py %*
exit /b 0
