import json
from collections import Counter

def load(path="app.log"):
    rows = []
    with open(path) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows

def p95(v):
    v = sorted(v)
    return v[max(int(len(v) * 0.95) - 1, 0)] if v else 0

def collect(path="app.log"):
    rows = load(path)
    changes = [r for r in rows if r["msg"] == "config changed"]
    cut = changes[-1]["ts"] if changes else None
    ok = [r for r in rows if r["msg"] == "checkout ok"]
    errs = [r for r in rows if r["level"] == "ERROR"]
    before = [r["latency_ms"] for r in ok if cut and r["ts"] < cut]
    after = [r["latency_ms"] for r in ok if cut and r["ts"] >= cut]
    return {
        "version": rows[0]["version"] if rows else None,
        "last_config_change": changes[-1] if changes else None,
        "p95_before_ms": p95(before),
        "p95_after_ms": p95(after),
        "error_count": len(errs),
        "top_errors": Counter(r["msg"] for r in errs).most_common(3),
        "first_error_secs_after_change": round(errs[0]["ts"] - cut, 1) if errs and cut else None,
    }

if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))