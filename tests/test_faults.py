import os, sys, time, threading, urllib.request
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent")))

from demo_app.main import app as demo_app
import core, collect, memory


@pytest.fixture(scope="session", autouse=True)
def ensure_server():
    running = False
    try:
        res = urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=1)
        if res.status == 200:
            running = True
    except Exception:
        pass

    if not running:
        import uvicorn
        config = uvicorn.Config(demo_app, host="127.0.0.1", port=8000, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(40):
            try:
                r = urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=1)
                if r.status == 200:
                    break
            except Exception:
                time.sleep(0.1)

    core.reset()
    yield
    core.reset()


@pytest.mark.parametrize("fault,right_fix,wrong_fix", [
    ("pool_exhaustion", "rollback_config", "scale_pool"),
    ("slow_query", "add_index", "scale_pool"),
    ("bad_deploy", "rollback_deploy", "restart_service"),
    ("memory_leak", "restart_service", "scale_pool"),
    ("downstream_timeout", "enable_circuit_breaker", "restart_service"),
    ("cache_disabled", "enable_cache", "scale_pool"),
])
def test_fault_remediation(fault, right_fix, wrong_fix):
    core.reset()

    # 1. Inject fault
    core.break_service(fault)
    ev = collect.collect()
    diagnosis = {"root_cause": f"simulated {fault}"}

    # 2. Apply wrong fix -> verify NOT recovered
    wrong_res = core.apply_fix(wrong_fix, diagnosis, ev, use_memory=False)
    assert not wrong_res["recovered"], f"Fault '{fault}' unexpectedly recovered with wrong fix '{wrong_fix}'"

    # 3. Apply right fix -> verify recovered
    right_res = core.apply_fix(right_fix, diagnosis, ev, use_memory=True)
    assert right_res["recovered"], f"Fault '{fault}' failed to recover with right fix '{right_fix}'"
    assert right_res["seconds_to_resolve"] is not None
    assert right_res["wrong_attempts"] >= 1


def test_signatures_differ_across_fault_types():
    faults = [
        "pool_exhaustion",
        "slow_query",
        "bad_deploy",
        "memory_leak",
        "downstream_timeout",
        "cache_disabled",
    ]
    signatures = {}

    for fault in faults:
        core.reset()
        core.break_service(fault)
        ev = collect.collect()
        sig = memory.signature(ev)
        signatures[fault] = sig

    # Verify that every fault produces a distinct signature
    unique_sigs = set(signatures.values())
    assert len(unique_sigs) == len(faults), (
        f"Expected {len(faults)} unique signatures, got {len(unique_sigs)}: {signatures}"
    )

