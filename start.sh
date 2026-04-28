#!/bin/bash

echo "==================================================="
echo "    Boolean Trader - Local Setup & Launcher"
echo "==================================================="
echo ""

# 1. Check for Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed or not in PATH."
    echo "Please install Python 3.10+ from python.org"
    exit 1
fi

# 2. Check for Node.js
if ! command -v npm &> /dev/null; then
    echo "[ERROR] Node.js is not installed or not in PATH."
    echo "Please install Node.js from nodejs.org"
    exit 1
fi

# 3. Setup .env file
if [ ! -f ".env" ]; then
    echo "[SETUP] No .env file found. Let's create one."
    echo "We need your OpenAI API key to run the agents."
    read -p "Enter your OpenAI API Key: " OPENAI_KEY
    echo "OPENAI_API_KEY=$OPENAI_KEY" > .env
    echo "[SUCCESS] .env file created!"
    echo ""
fi

# 4. Setup Python Virtual Environment
if [ ! -d "venv" ]; then
    echo "[SETUP] Creating Python virtual environment..."
    python3 -m venv venv
    echo "[SUCCESS] Virtual environment created!"
    echo ""
fi

# 5. Install Dependencies
echo "[SETUP] Installing/Updating Python dependencies..."
source venv/bin/activate
pip install -r requirements.txt > /dev/null
echo "[SUCCESS] Backend dependencies ready!"
echo ""

echo "[SETUP] Installing/Updating Frontend dependencies..."
cd frontend
npm install > /dev/null
cd ..
echo "[SUCCESS] Frontend dependencies ready!"
echo ""

# 6. Launch Application
echo "==================================================="
echo "    Starting Boolean Trader..."
echo "    Press Ctrl+C to stop both servers."
echo "==================================================="
echo ""

# Start FastAPI backend in the background
source venv/bin/activate
uvicorn api.main:app --reload &
BACKEND_PID=$!

# Start Vite frontend in the foreground
cd frontend
echo "[INFO] Starting Web Interface..."
npm run dev

# Trap Ctrl+C to kill the backend when the frontend stops
trap "kill $BACKEND_PID" EXIT
