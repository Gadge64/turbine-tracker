@echo off
:: deploy.bat
:: Double-click this file to push all code changes to GitHub AND make them
:: live on the website automatically. It does three things:
::   1. Push the latest code to GitHub
::   2. Tell the live server to pull that code (via a hidden /deploy route)
::   3. Reload the website so visitors see the new version immediately

cd /d "%~dp0"

:: Check that the API key file exists before we start
if not exist pa_api_key.txt (
    echo.
    echo  ERROR: pa_api_key.txt not found.
    echo  This file should contain your PythonAnywhere API token.
    echo.
    pause
    exit /b 1
)

:: Read the PythonAnywhere API key and deploy secret from the local config file.
:: These are stored locally and never pushed to GitHub.
set /p PA_TOKEN=<pa_api_key.txt
:: The deploy secret must match what is set as DEPLOY_SECRET on PythonAnywhere
set DEPLOY_SECRET=turb1ne-depl0y-2026

echo.
echo  =============================================
echo    Turbine Tracker - Deploy to Live Site
echo  =============================================
echo.

:: ── Step 1: Push to GitHub ────────────────────────────────────────────────────
echo  [1/3] Pushing code to GitHub...
git push origin master
if errorlevel 1 (
    echo.
    echo  ERROR: Git push failed.
    echo  Make sure all changes are committed before deploying.
    echo.
    pause
    exit /b 1
)
echo        Pushed successfully.
echo.

:: ── Steps 2 and 3: Pull on server + reload ────────────────────────────────────
powershell -ExecutionPolicy Bypass -File "%~dp0deploy_pa.ps1" -Token "%PA_TOKEN%" -DeploySecret "%DEPLOY_SECRET%"

echo.
echo  =============================================
echo   Live at: https://gadge64.pythonanywhere.com
echo  =============================================
echo.
pause
