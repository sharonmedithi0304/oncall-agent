import time, json, logging, threading
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import TimeoutError as PoolTimeout

VERSION = "v3.8.2"
app = FastAPI()
engine = create_engine(
    "sqlite:///shop.db", poolclass=QueuePool,
    pool_size=5, max_overflow=0, pool_timeout=1,
    connect_args={"check_same_thread": False},
)
with engine.begin() as c:
    c.execute(text("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT, qty INT)"))

FAULTS = {"pool": False}
LAT, ERRS = [], [0]
lock = threading.Lock()

logging.basicConfig(filename="app.log", level=logging.INFO, format="%(message)s")


def log(level, msg, **kw):
    logging.info(json.dumps({"ts": time.time(), "level": level, "msg": msg, "version": VERSION, **kw}))


log("INFO", "deploy complete")


@app.post("/checkout")
def checkout(item: str = "book", qty: int = 1):
    t = time.time()
    try:
        with engine.connect() as conn:
            if FAULTS["pool"]:
                time.sleep(2)  # simulates connections held too long
            conn.execute(text("INSERT INTO orders (item, qty) VALUES (:i,:q)"), {"i": item, "q": qty})
            conn.commit()
    except PoolTimeout:
        with lock:
            ERRS[0] += 1
        log("ERROR", "QueuePool limit reached, connection timed out", pool_size=5)
        raise HTTPException(503, "db pool exhausted")
    ms = round((time.time() - t) * 1000)
    with lock:
        LAT.append(ms)
    log("INFO", "checkout ok", latency_ms=ms)
    return {"ok": True, "latency_ms": ms}


@app.post("/fault/pool")
def fault_pool(on: bool = True):
    FAULTS["pool"] = on
    log("WARN", "config changed", pool_hold_s=2 if on else 0)
    return FAULTS


@app.get("/metrics")
def metrics():
    with lock:
        recent = sorted(LAT[-50:])
        p95 = recent[max(int(len(recent) * 0.95) - 1, 0)] if recent else 0
        return {"p95_ms": p95, "errors": ERRS[0], "pool_in_use": engine.pool.checkedout()}
