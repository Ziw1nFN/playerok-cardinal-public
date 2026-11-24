@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist venv (
    echo Run Setup.bat first
    pause
    exit /b 1
)
venv\Scripts\python main.py
pause
