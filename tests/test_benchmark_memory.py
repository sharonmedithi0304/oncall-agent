import json
import os
import sys
import threading
import time
import urllib.request
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "benchmark")))

from demo_app.main import app as demo_app
import core
import collect
import diagnose as dg
import memory
from benchmark.run import run_benchmark, ALL_FAULTS


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


def test_empty_memory_both_arms_choose_same_first_action():
    """Requirement (a): with empty memory both arms choose the same first action."""
    test_mem_path = "test_empty_mem.json"
    memory.clear(test_mem_path)

    for fault in ALL_FAULTS:
        core.reset()
        core.break_service(fault)
        ev = collect.collect()

        # Diagnose without memory
        d_off = dg.heuristic(ev, past=[], tried=None)

        # Diagnose with memory (which is empty)
        past_empty = memory.recall(ev, path=test_mem_path)
        assert len(past_empty) == 0
        d_on = dg.heuristic(ev, past=past_empty, tried=None)

        assert d_off["action"] == d_on["action"], (
            f"Fault {fault}: expected identical first action, got {d_off['action']} (off) vs {d_on['action']} (on)"
        )
        assert d_off["root_cause"] == d_on["root_cause"]

    memory.clear(test_mem_path)


def test_memory_arm_never_repeats_failed_fix_on_same_signature():
    """Requirement (b): after a failed fix, the memory arm never repeats it on the same signature."""
    test_mem_path = "test_fail_repeat_mem.json"
    memory.clear(test_mem_path)

    fault = "pool_exhaustion"
    core.reset()
    core.break_service(fault)
    ev = collect.collect()

    # Step 1: Diagnose with empty memory -> picks prior "scale_pool"
    d_initial = dg.heuristic(ev, past=[], tried=None)
    first_action = d_initial["action"]
    assert first_action == "scale_pool"

    # Step 2: Record that first_action FAILED in memory for this signature
    memory.remember({
        "signature": memory.signature(ev),
        "root_cause": d_initial["root_cause"],
        "action": first_action,
        "worked": False,
        "before": {"p95_ms": 1000, "errors": 5},
        "after": {"p95_ms": 1000, "errors": 5},
        "seconds_to_resolve": 2.0,
        "wrong_attempts": 1,
        "memory_on": True,
    }, path=test_mem_path)

    # Step 3: Now on a new occurrence of this signature, diagnose with memory recalled
    recalled_past = memory.recall(ev, path=test_mem_path)
    assert len(recalled_past) == 1
    assert recalled_past[0]["worked"] is False

    d_new = dg.heuristic(ev, past=recalled_past, tried=None)
    # The memory arm must NEVER repeat the failed action
    assert d_new["action"] != first_action, (
        f"Memory arm repeated failed action '{first_action}' on the same signature"
    )
    assert d_new["action"] in ["restart_service", "rollback_config"]

    memory.clear(test_mem_path)


def test_results_json_schema(tmp_path):
    """Requirement (c): results.json has the expected schema."""
    out_json = str(tmp_path / "results.json")
    out_csv = str(tmp_path / "results.csv")

    results = run_benchmark(rounds=1, seed=42, fast=True, output_json=out_json, output_csv=out_csv)

    # Verify file was written
    assert os.path.exists(out_json)
    with open(out_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Root keys
    for key in ["timestamp", "rounds", "seed", "summary", "per_round", "incidents"]:
        assert key in data, f"Missing root key: {key}"

    assert data["rounds"] == 1
    assert data["seed"] == 42

    # Summary schema
    summary = data["summary"]
    for arm in ["memory_off", "memory_on"]:
        assert arm in summary
        arm_stats = summary[arm]
        for field in ["first_try_success_rate", "avg_wrong_attempts", "avg_seconds_to_recovery", "total_incidents"]:
            assert field in arm_stats, f"Missing {field} in summary.{arm}"
        assert isinstance(arm_stats["first_try_success_rate"], (int, float))
        assert isinstance(arm_stats["avg_wrong_attempts"], (int, float))
        assert isinstance(arm_stats["avg_seconds_to_recovery"], (int, float))
        assert arm_stats["total_incidents"] == 6

    assert "headline" in summary
    assert isinstance(summary["headline"], str)
    assert "Memory cut" in summary["headline"]

    # Per_round schema
    per_round = data["per_round"]
    assert len(per_round) == 1
    r1 = per_round[0]
    assert r1["round"] == 1
    assert "memory_off" in r1 and "memory_on" in r1
    for arm in ["memory_off", "memory_on"]:
        for field in ["first_try_success_rate", "avg_wrong_attempts", "avg_seconds", "incidents"]:
            assert field in r1[arm], f"Missing {field} in per_round[0].{arm}"

    # Incidents schema
    incidents = data["incidents"]
    assert len(incidents) == 12  # 6 faults * 1 round * 2 arms
    for inc in incidents:
        for field in ["arm", "round", "fault", "recovered", "attempts", "first_try_success", "wrong_attempts", "seconds_to_resolve", "tried_actions"]:
            assert field in inc, f"Missing incident field {field}"
        assert inc["arm"] in ["memory_off", "memory_on"]
        assert isinstance(inc["recovered"], bool)
        assert isinstance(inc["first_try_success"], bool)
        assert isinstance(inc["tried_actions"], list)

