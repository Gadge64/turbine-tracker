@echo off
:: deploy.bat
:: Double-click this file to push all code changes to GitHub AND make them
:: live on the website automatically. It does three things in order:
::   1. Push the latest code to GitHub
::   2. Tell PythonAnywhere to pull that code onto the live server
::   3. Reload the website so visitors see the new version

cd /d "%~dp0"

:: Check that the API key file exists before we do anything
if not exist pa_api_key.txt (
    echo.
    echo  ERROR: pa_api_key.txt not found.
    echo  This file should contain your PythonAnywhere API token.
    echo.
    pause
    exit /b 1
)

:: Read the API key from the local file into a variable
set /p PA_TOKEN=<pa_api_key.txt

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
    echo  Make sure you have committed your changes first.
    echo.
    pause
    exit /b 1
)
echo        Pushed successfully.
echo.

:: ── Steps 2 and 3: PythonAnywhere pull + reload ───────────────────────────────
:: We hand off to the PowerShell script which handles the API calls.
:: -ExecutionPolicy Bypass allows the script to run without needing to change
:: Windows security settings.
powershell -ExecutionPolicy Bypass -File "%~dp0deploy_pa.ps1" -Token "%PA_TOKEN%"

echo.
echo  =============================================
echo   Live at: https://gadge64.pythonanywhere.com
echo  =============================================
echo.
pause
