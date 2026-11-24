@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing Playerok Cardinal...
if not exist venv (
    python -m venv venv || (echo Python 3.11+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
)
venv\Scripts\python -m pip install --upgrade pip
venv\Scripts\python -m pip install -r requirements.txt
if not exist .env venv\Scripts\python first_setup.py
echo.
echo Done! Run Start.bat
pause
