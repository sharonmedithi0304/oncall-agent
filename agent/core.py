import json, time, threading, urllib.request
from memory import remember, signature

BASE = "http://127.0.0.1:8000"
INCIDENT = {"started": None, "wrong_attempts": 0}


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


def break_service(fault="pool_exhaustion"):
    _req("/reset", "POST")  # start every incident from a clean, reproducible state
    if fault == "pool":
        fault = "pool_exhaustion"
    _req(f"/fault/{fault}?on=true", "POST")
    INCIDENT["started"] = time.time()
    INCIDENT["wrong_attempts"] = 0
    return burst(20)


def reset():
    _req("/reset", "POST")
    INCIDENT["started"] = None
    INCIDENT["wrong_attempts"] = 0


def apply_fix(action, diagnosis, evidence, use_memory):
    before = status()
    _req(f"/fix/{action}", "POST")
    after = burst(10)
    recovered = after["errors"] == 0 and after["p95_ms"] is not None and after["p95_ms"] < 500
    secs = round(time.time() - INCIDENT["started"], 1) if INCIDENT["started"] else None

    if not recovered:
        INCIDENT["wrong_attempts"] += 1
    wrong_attempts = INCIDENT["wrong_attempts"]

    result = {
        "action": action,
        "before": {"p95_ms": before["p95_ms"], "errors": before["errors"]},
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
    })
    return result
