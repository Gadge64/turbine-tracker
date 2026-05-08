@echo off
cd /d "%~dp0"

REM Set a recovery password of your choosing before running this script:
set RECOVERY_ADMIN_USERNAME=recovery_admin
set RECOVERY_ADMIN_PASSWORD=ChangeThisBeforeUse!

if not exist venv (
    python -m venv venv
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py

pause
