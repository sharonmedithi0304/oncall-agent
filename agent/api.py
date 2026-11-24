import os, sys, threading, time, urllib.request, uuid, json
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
import core, memory, diagnose as dg

INCIDENTS = []
watcher_lock = threading.Lock()

BENCHMARK_STATUS = {
    "running": False,
    "completed": 0,
    "total": 0,
    "percent": 0.0,
    "arm": None,
    "fault": None,
    "round": None,
    "error": None,
}
benchmark_lock = threading.Lock()


def _ping():
    try:
        urllib.request.urlopen(urllib.request.Request(core.BASE + "/checkout", method="POST"), timeout=10)
    except Exception:
        pass


def _traffic():
    while True:
        threading.Thread(target=_ping, daemon=True).start()
        time.sleep(0.8)


def _watcher():
    consecutive_degraded = 0
    while True:
        time.sleep(2)
        try:
            m = core.status()
            degraded = (m.get("p95_ms", 0) > 500) or (m.get("errors", 0) > 0)
            if degraded:
                consecutive_degraded += 1
            else:
                consecutive_degraded = 0

            # If p95>500ms or errors>0 for 3 consecutive polls, open an incident and run diagnose automatically
            if consecutive_degraded >= 3:
                with watcher_lock:
                    has_open = any(inc.get("status") == "detected" for inc in INCIDENTS)
                    if not has_open:
                        inc_id = f"inc-{uuid.uuid4().hex[:6]}"
                        now = time.time()
                        inc = {
                            "id": inc_id,
                            "started_at": now,
                            "status": "detected",
                            "diagnosis": None,
                            "evidence": None,
                        }
                        INCIDENTS.append(inc)
                        core.INCIDENT["started"] = now
                        core.INCIDENT["wrong_attempts"] = 0

                        # Run diagnose automatically without applying the fix (wait for human approval)
                        try:
                            ev, d = dg.diagnose(use_memory=True)
                            inc["evidence"] = ev
                            inc["diagnosis"] = d
                        except Exception as e:
                            print(f"[watcher] auto-diagnose failed: {e}")
        except Exception:
            pass


def _run_benchmark_task(rounds=3, seed=42):
    from benchmark.run import run_benchmark
    def _cb(p):
        with benchmark_lock:
            BENCHMARK_STATUS.update(p)

    try:
        run_benchmark(rounds=rounds, seed=seed, fast=True, progress_callback=_cb)
        with benchmark_lock:
            BENCHMARK_STATUS["running"] = False
            BENCHMARK_STATUS["percent"] = 100.0
    except Exception as e:
        with benchmark_lock:
            BENCHMARK_STATUS["running"] = False
            BENCHMARK_STATUS["error"] = str(e)


@asynccontextmanager
async def lifespan(app):
    if os.environ.get("LIVE_TRAFFIC", "1") == "1":
        threading.Thread(target=_traffic, daemon=True).start()
    threading.Thread(target=_watcher, daemon=True).start()
    yield


app = FastAPI(title="On-call agent API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/state")
def state():
    base = {"mode": dg.mode(), "memory_count": len(memory.load())}
    try:
        return {"online": True, **base, **core.status()}
    except Exception:
        return {"online": False, **base}


@app.post("/api/break")
async def break_it(request: Request, fault: str = "pool_exhaustion"):
    try:
        body = await request.json()
        if isinstance(body, dict) and "fault" in body:
            fault = body["fault"]
    except Exception:
        pass

    try:
        return core.break_service(fault)
    except Exception as e:
        raise HTTPException(502, f"Demo service unreachable: {e}")


@app.post("/api/reset")
def reset():
    try:
        core.reset()
        with watcher_lock:
            for inc in INCIDENTS:
                if inc.get("status") == "detected":
                    inc["status"] = "dismissed"
    except Exception as e:
        raise HTTPException(502, f"Demo service unreachable: {e}")
    return {"ok": True}


@app.post("/api/diagnose")
def diagnose(use_memory: bool = True):
    ev, d = dg.diagnose(use_memory)
    if not ev["incident_detected"]:
        raise HTTPException(400, "No incident evidence yet. Click 'Break service' first.")
    return {"evidence": ev, "diagnosis": d, "use_memory": use_memory}


@app.post("/api/fix")
def fix(body: dict = Body(...)):
    try:
        res = core.apply_fix(
            body["action"],
            body.get("diagnosis", {}),
            body.get("evidence", {}),
            body.get("use_memory", True),
        )
        if res.get("recovered"):
            with watcher_lock:
                for inc in INCIDENTS:
                    if inc.get("status") == "detected":
                        inc["status"] = "resolved"
                        inc["resolved_at"] = time.time()
        return res
    except Exception as e:
        raise HTTPException(502, f"Fix failed: {e}")


@app.get("/api/incidents")
def list_incidents():
    return INCIDENTS


@app.get("/api/memory")
def get_memory():
    return memory.load()


@app.delete("/api/memory")
def clear_memory():
    memory.clear()
    return {"ok": True}


# Benchmark endpoints
@app.get("/api/benchmark")
def get_benchmark():
    res_path = os.path.join(ROOT_DIR, "benchmark", "results.json")
    if os.path.exists(res_path):
        try:
            with open(res_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            raise HTTPException(500, f"Error reading benchmark results: {e}")
    return {}


@app.post("/api/benchmark/run")
def start_benchmark(body: dict = Body(default={})):
    rounds = body.get("rounds", 3)
    seed = body.get("seed", 42)
    with benchmark_lock:
        if BENCHMARK_STATUS["running"]:
            return {"status": "already_running", "benchmark": BENCHMARK_STATUS}
        BENCHMARK_STATUS.update({
            "running": True,
            "completed": 0,
            "total": rounds * 6 * 2,
            "percent": 0.0,
            "arm": None,
            "fault": None,
            "round": None,
            "error": None,
        })
    t = threading.Thread(target=_run_benchmark_task, args=(rounds, seed), daemon=True)
    t.start()
    return {"status": "started", "rounds": rounds, "seed": seed}


@app.get("/api/benchmark/status")
def get_benchmark_status():
    with benchmark_lock:
        return dict(BENCHMARK_STATUS)
