@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo     Boolean Trader - Local Setup ^& Launcher
echo ===================================================
echo.

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ from python.org
    pause
    exit /b
)

:: 2. Check for Node.js
npm --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo Please install Node.js from nodejs.org
    pause
    exit /b
)

:: 3. Setup .env file
if not exist ".env" (
    echo [SETUP] No .env file found. Let's create one.
    echo We need your OpenAI API key to run the agents.
    set /p OPENAI_KEY="Enter your OpenAI API Key: "
    echo OPENAI_API_KEY=!OPENAI_KEY!> .env
    echo [SUCCESS] .env file created!
    echo.
)

:: 4. Setup Python Virtual Environment
if not exist "venv" (
    echo [SETUP] Creating Python virtual environment...
    python -m venv venv
    echo [SUCCESS] Virtual environment created!
    echo.
)

:: 5. Install Dependencies
echo [SETUP] Installing/Updating Python dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt >nul
echo [SUCCESS] Backend dependencies ready!
echo.

echo [SETUP] Installing/Updating Frontend dependencies...
cd frontend
call npm install >nul
cd ..
echo [SUCCESS] Frontend dependencies ready!
echo.

:: 6. Launch Application
echo ===================================================
echo     Starting Boolean Trader...
echo     Do not close these terminal windows.
echo ===================================================
echo.

:: Start FastAPI backend in a new command window
start "Boolean Trader Backend" cmd /k "call venv\Scripts\activate.bat && uvicorn api.main:app --reload"

:: Start Vite frontend in the current window
cd frontend
echo [INFO] Starting Web Interface...
call npm run dev
