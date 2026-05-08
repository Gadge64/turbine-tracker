@echo off
cd /d "%~dp0"

set RECOVERY_ADMIN_USERNAME=recovery_admin
set RECOVERY_ADMIN_PASSWORD=TempReset123!

if not exist venv (
    python -m venv venv
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py

pause
