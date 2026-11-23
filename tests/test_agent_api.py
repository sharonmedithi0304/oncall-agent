import os, sys, time, threading, urllib.request, json
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent")))

from demo_app.main import app as demo_app
from agent.api import app as agent_app
import core


@pytest.fixture(scope="session", autouse=True)
def ensure_servers():
    import uvicorn
    # Start demo_app on 8000
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=1)
    except Exception:
        config_demo = uvicorn.Config(demo_app, host="127.0.0.1", port=8000, log_level="warning")
        server_demo = uvicorn.Server(config_demo)
        t_demo = threading.Thread(target=server_demo.run, daemon=True)
        t_demo.start()

    # Start agent_app on 8001
    try:
        urllib.request.urlopen("http://127.0.0.1:8001/api/state", timeout=1)
    except Exception:
        config_agent = uvicorn.Config(agent_app, host="127.0.0.1", port=8001, log_level="warning")
        server_agent = uvicorn.Server(config_agent)
        t_agent = threading.Thread(target=server_agent.run, daemon=True)
        t_agent.start()

    for _ in range(40):
        try:
            r1 = urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=1)
            r2 = urllib.request.urlopen("http://127.0.0.1:8001/api/state", timeout=1)
            if r1.status == 200 and r2.status == 200:
                break
        except Exception:
            time.sleep(0.1)

    core.reset()
    yield
    core.reset()


def _api_get(path):
    req = urllib.request.Request(f"http://127.0.0.1:8001{path}")
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())


def _api_post(path, data=None):
    payload = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(f"http://127.0.0.1:8001{path}", data=payload, headers=headers, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=25).read().decode())


def test_api_state_and_memory():
    st = _api_get("/api/state")
    assert st["online"] is True
    assert "memory_mb" in st
    assert "active_faults" in st
    assert "version" in st


def test_api_break_and_diagnose():
    # Reset
    _api_post("/api/reset")

    # Break with bad_deploy
    _api_post("/api/break", {"fault": "bad_deploy"})

    # Diagnose
    diag_res = _api_post("/api/diagnose?use_memory=true")
    assert diag_res["evidence"]["incident_detected"] is True
    assert diag_res["diagnosis"]["action"] == "rollback_deploy"

    # Fix
    fix_res = _api_post("/api/fix", {
        "action": "rollback_deploy",
        "diagnosis": diag_res["diagnosis"],
        "evidence": diag_res["evidence"],
        "use_memory": True,
    })
    assert fix_res["recovered"] is True


def test_api_auto_detection():
    # Reset
    _api_post("/api/reset")

    # Inject slow_query fault
    _api_post("/api/break", {"fault": "slow_query"})

    # Watcher polls every 2s, triggers after 3 consecutive degraded polls (~6-8s)
    # Wait for incident to be auto-detected
    detected = False
    for _ in range(15):
        time.sleep(1.5)
        incidents = _api_get("/api/incidents")
        open_incs = [i for i in incidents if i["status"] == "detected"]
        if open_incs:
            detected = True
            inc = open_incs[0]
            assert inc["diagnosis"] is not None
            assert inc["evidence"] is not None
            break

    assert detected, "Watcher should auto-detect incident within 15s of sustained degradation"

