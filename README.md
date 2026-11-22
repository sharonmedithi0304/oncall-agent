# oncall-agent

Run (cmd, venv active):
    pip install -r requirements.txt
    uvicorn demo_app.main:app --reload

Second cmd window:
    python loadtest.py            # healthy baseline
    curl -X POST "http://127.0.0.1:8000/fault/pool?on=true"
    python loadtest.py            # now it breaks
    curl -X POST "http://127.0.0.1:8000/fault/pool?on=false"
