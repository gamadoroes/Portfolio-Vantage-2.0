@echo off
setlocal

cd /d "%~dp0"

set "VENV_DIR=.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo Creating virtual environment...
    py -3 -m venv "%VENV_DIR%" 2>nul
    if errorlevel 1 python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create a virtual environment.
        pause
        exit /b 1
    )
)

echo Installing dependencies...
"%VENV_PYTHON%" -m pip install -r requirements_flask.txt
if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
)

if "%FLASK_DEBUG%"=="" set "FLASK_DEBUG=0"

echo.
echo Starting Research Architect...
echo.
echo The app will open at: http://localhost:5000
echo Press Ctrl+C to stop the server
echo.

"%VENV_PYTHON%" app.py

pause
