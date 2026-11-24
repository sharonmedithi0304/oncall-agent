import json, time, threading, urllib.request, os
from memory import remember, signature

BASE = "http://127.0.0.1:8000"
INCIDENT = {"started": None, "wrong_attempts": 0}
FAST_MODE = False


def set_fast_mode(enabled: bool):
    global FAST_MODE
    FAST_MODE = enabled
    try:
        _req(f"/fast_mode?enabled={'true' if enabled else 'false'}", "POST")
    except Exception:
        pass


def _req(path, method="GET", timeout=25):
    req = urllib.request.Request(BASE + path, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")


def status():
    return _req("/metrics")


def _one(results, lock):
    t = time.time()
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + "/checkout", method="POST"), timeout=15)
        ok = True
    except Exception:
        ok = False
    with lock:
        results.append((ok, round((time.time() - t) * 1000)))


def burst(n=20):
    results, lock = [], threading.Lock()
    ts = [threading.Thread(target=_one, args=(results, lock)) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    lat = sorted(ms for ok, ms in results if ok)
    p95 = lat[max(int(len(lat) * 0.95) - 1, 0)] if lat else None
    return {"requests": n, "errors": sum(1 for ok, _ in results if not ok), "p95_ms": p95}


def break_service(fault="pool_exhaustion", burst_n=None):
    _req("/reset", "POST")  # start every incident from a clean, reproducible state
    if fault == "pool":
        fault = "pool_exhaustion"
    _req(f"/fault/{fault}?on=true", "POST")
    INCIDENT["started"] = time.time()
    INCIDENT["wrong_attempts"] = 0
    n = burst_n if burst_n is not None else (10 if FAST_MODE else 20)
    return burst(n)


def reset():
    _req("/reset", "POST")
    INCIDENT["started"] = None
    INCIDENT["wrong_attempts"] = 0


def apply_fix(action, diagnosis, evidence, use_memory, memory_path=None, burst_n=None):
    before = status()
    _req(f"/fix/{action}", "POST")
    n = burst_n if burst_n is not None else (8 if FAST_MODE else 10)
    after = burst(n)
    threshold = 200 if FAST_MODE else 500
    recovered = after["errors"] == 0 and after["p95_ms"] is not None and after["p95_ms"] < threshold
    secs = round(time.time() - INCIDENT["started"], 2) if INCIDENT["started"] else None

    if not recovered:
        INCIDENT["wrong_attempts"] += 1
    wrong_attempts = INCIDENT["wrong_attempts"]

    result = {
        "action": action,
        "before": {"p95_ms": before.get("p95_ms", 0), "errors": before.get("errors", 0)},
        "after": after,
        "recovered": recovered,
        "seconds_to_resolve": secs,
        "wrong_attempts": wrong_attempts,
        "memory_on": use_memory,
    }
    remember({
        "signature": signature(evidence),
        "root_cause": diagnosis.get("root_cause"),
        "action": action,
        "worked": recovered,
        "before": result["before"],
        "after": after,
        "seconds_to_resolve": secs,
        "wrong_attempts": wrong_attempts,
        "memory_on": use_memory,
    }, path=memory_path)
    return result
