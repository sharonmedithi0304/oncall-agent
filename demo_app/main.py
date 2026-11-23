import time, json, logging, threading, random, os
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import TimeoutError as PoolTimeout

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(BASE_DIR, "app.log")

BASE_VERSION = "v3.8.2"
VERSION = BASE_VERSION
WINDOW_S = 30
app = FastAPI(title="Checkout service (demo)")


def make_engine(size):
    return create_engine(
        "sqlite:///shop.db", poolclass=QueuePool,
        pool_size=size, max_overflow=0, pool_timeout=1,
        connect_args={"check_same_thread": False},
    )


S = {"engine": make_engine(5), "pool_size": 5}
with S["engine"].begin() as c:
    c.execute(text("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT, qty INT)"))

FAULTS = {
    "pool_exhaustion": False,
    "slow_query": False,
    "bad_deploy": False,
    "memory_leak": False,
    "downstream_timeout": False,
    "cache_disabled": False,
}

LEAK_BUFFER = []
LAT, ERR = [], []
lock = threading.Lock()

# Configure dedicated file handler to guarantee log output even under pytest/uvicorn
app_logger = logging.getLogger("checkout_demo")
app_logger.setLevel(logging.INFO)
app_logger.propagate = False

_file_handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(message)s"))
app_logger.handlers = [_file_handler]


def log(level, msg, **kw):
    entry = json.dumps({"ts": time.time(), "level": level, "msg": msg, "version": VERSION, **kw})
    app_logger.info(entry)
    _file_handler.flush()


log("INFO", "deploy complete", version=VERSION)


def get_memory_mb():
    return round(120.0 + len(LEAK_BUFFER) * 1.0, 1)


@app.post("/checkout")
def checkout(item: str = "book", qty: int = 1):
    global VERSION
    t = time.time()

    # Fault 3: bad_deploy - ~40% of checkouts return 500 with NullReference in apply_discount
    if FAULTS["bad_deploy"] and random.random() < 0.40:
        with lock:
            ERR.append(time.time())
        log("ERROR", "NullReference in apply_discount", version=VERSION, item=item)
        raise HTTPException(500, "Internal Server Error: NullReference in apply_discount")

    # Fault 5: downstream_timeout - payment provider call sleeps 3s then raises
    if FAULTS["downstream_timeout"]:
        time.sleep(3)
        with lock:
            ERR.append(time.time())
        log("ERROR", "payment-gateway timeout", downstream="payment-gateway", timeout_s=3)
        raise HTTPException(504, "payment-gateway timeout")

    # Fault 2: slow_query - sleep 0.8-1.5s, no errors
    if FAULTS["slow_query"]:
        delay = random.uniform(0.8, 1.5)
        time.sleep(delay)
        log("WARN", "slow query on orders: sequential scan without index", query="SELECT * FROM orders WHERE item = :i", duration_s=round(delay, 3))

    # Fault 6: cache_disabled - cache flag is off so every request hits DB (latency up, no errors)
    if FAULTS["cache_disabled"]:
        delay = random.uniform(0.6, 0.8)
        time.sleep(delay)
        log("WARN", "cache disabled: cache miss query fallback to db", cache_enabled=False, duration_s=round(delay, 3))

    # Fault 4: memory_leak - global list grows on each request, latency creeps up as memory_mb rises
    if FAULTS["memory_leak"]:
        LEAK_BUFFER.extend([b"x" * 1024] * 15)
        mem_mb = get_memory_mb()
        if mem_mb >= 850.0:  # 85% of 1000MB limit
            log("WARN", "memory 85% of limit", memory_mb=mem_mb, limit_mb=1000)
            creep_delay = min(0.6 + (mem_mb - 850.0) * 0.005, 1.8)
            time.sleep(creep_delay)

    # Fault 1: pool_exhaustion (existing: connections held too long)
    try:
        with S["engine"].connect() as conn:
            if FAULTS["pool_exhaustion"]:
                time.sleep(2)  # simulates connections held too long
            conn.execute(text("INSERT INTO orders (item, qty) VALUES (:i,:q)"), {"i": item, "q": qty})
            conn.commit()
    except PoolTimeout:
        with lock:
            ERR.append(time.time())
        log("ERROR", "QueuePool limit reached, connection timed out", pool_size=S["pool_size"])
        raise HTTPException(503, "db pool exhausted")

    ms = round((time.time() - t) * 1000)
    with lock:
        LAT.append((time.time(), ms))
    log("INFO", "checkout ok", latency_ms=ms)
    return {"ok": True, "latency_ms": ms}


@app.post("/fault/{name}")
def set_fault(name: str, on: bool = True):
    global VERSION
    if name == "pool":
        name = "pool_exhaustion"
    if name not in FAULTS:
        raise HTTPException(404, f"unknown fault {name}")

    FAULTS[name] = on

    if name == "pool_exhaustion":
        log("WARN", "config changed", pool_hold_s=2 if on else 0)
    elif name == "slow_query":
        log("WARN", "database index dropped on orders(item)" if on else "database index restored on orders(item)")
    elif name == "bad_deploy":
        VERSION = "v3.8.3" if on else BASE_VERSION
        log("INFO", "deploy complete", version=VERSION)
    elif name == "memory_leak":
        if on:
            LEAK_BUFFER.extend([b"x" * 1024] * 740)
            log("WARN", "memory leak simulation started", memory_mb=get_memory_mb())
        else:
            LEAK_BUFFER.clear()
            log("INFO", "memory leak simulation stopped")
    elif name == "downstream_timeout":
        log("WARN", "downstream payment-gateway dependency degraded" if on else "downstream payment-gateway healthy")
    elif name == "cache_disabled":
        log("WARN", "cache cluster disconnected, cache disabled" if on else "cache cluster reconnected, cache enabled")

    return {
        "fault": name,
        "on": on,
        "active_faults": [k for k, v in FAULTS.items() if v],
    }


@app.post("/fault/pool")
def fault_pool(on: bool = True):
    return set_fault("pool_exhaustion", on)


@app.post("/fix/{action}")
def fix(action: str):
    global VERSION
    valid_actions = {
        "rollback_config",
        "scale_pool",
        "restart_service",
        "add_index",
        "rollback_deploy",
        "enable_circuit_breaker",
        "enable_cache",
    }
    if action not in valid_actions:
        raise HTTPException(400, f"unknown action {action}")

    cured = False

    # Each fix endpoint cures ONLY its matching fault
    if action == "rollback_config" and FAULTS["pool_exhaustion"]:
        FAULTS["pool_exhaustion"] = False
        cured = True
    elif action == "add_index" and FAULTS["slow_query"]:
        FAULTS["slow_query"] = False
        cured = True
    elif action == "rollback_deploy" and FAULTS["bad_deploy"]:
        FAULTS["bad_deploy"] = False
        VERSION = BASE_VERSION
        cured = True
    elif action == "restart_service" and FAULTS["memory_leak"]:
        FAULTS["memory_leak"] = False
        LEAK_BUFFER.clear()
        S["engine"].dispose()
        cured = True
    elif action == "enable_circuit_breaker" and FAULTS["downstream_timeout"]:
        FAULTS["downstream_timeout"] = False
        cured = True
    elif action == "enable_cache" and FAULTS["cache_disabled"]:
        FAULTS["cache_disabled"] = False
        cured = True
    elif action == "scale_pool":
        S["engine"] = make_engine(20)
        S["pool_size"] = 20
        cured = False

    with lock:
        LAT.clear()
        ERR.clear()

    if cured:
        log("INFO", "fix applied", action=action, pool_size=S["pool_size"], version=VERSION)
    else:
        log("WARN", "fix applied, no change", action=action)

    return {
        "applied": action,
        "cured": cured,
        "fault": any(FAULTS.values()),
        "active_faults": [k for k, v in FAULTS.items() if v],
        "pool_size": S["pool_size"],
    }


@app.post("/reset")
def reset():
    global VERSION
    for k in FAULTS:
        FAULTS[k] = False
    VERSION = BASE_VERSION
    LEAK_BUFFER.clear()
    S["engine"] = make_engine(5)
    S["pool_size"] = 5
    with lock:
        LAT.clear()
        ERR.clear()
    log("INFO", "service reset", version=VERSION)
    return {"ok": True}


@app.get("/metrics")
def metrics():
    now = time.time()
    with lock:
        lat = sorted(ms for ts, ms in LAT if now - ts < WINDOW_S)
        errs = sum(1 for ts in ERR if now - ts < WINDOW_S)
        LAT[:] = [x for x in LAT if now - x[0] < 120]
        ERR[:] = [x for x in ERR if now - x < 120]
    p95 = lat[max(int(len(lat) * 0.95) - 1, 0)] if lat else 0
    active = [k for k, v in FAULTS.items() if v]
    return {
        "p95_ms": p95,
        "errors": errs,
        "pool_in_use": S["engine"].pool.checkedout(),
        "pool_size": S["pool_size"],
        "fault": bool(active),
        "active_faults": active,
        "memory_mb": get_memory_mb(),
        "version": VERSION,
    }
