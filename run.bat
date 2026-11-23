@echo off
REM Starts everything. Run from the project folder (with your GEMINI_API_KEY set in this window if you want the real LLM).
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate
pip install -q -r requirements.txt

start "demo-app :8000" cmd /k "call .venv\Scripts\activate && uvicorn demo_app.main:app --port 8000"
timeout /t 3 >nul
start "agent-api :8001" cmd /k "call .venv\Scripts\activate && uvicorn agent.api:app --port 8001"

cd dashboard
if not exist node_modules (
  echo Installing dashboard packages, first time only...
  call npm install
)
start "dashboard :5173" cmd /k "npm run dev"
timeout /t 5 >nul
start http://localhost:5173
echo.
echo Three windows opened. Close them to stop everything.
