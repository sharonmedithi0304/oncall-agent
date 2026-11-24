import json, os
from collections import Counter
import core

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG_PATH = os.path.join(BASE_DIR, "app.log")


def load(path=None):
    if path is None or path == "app.log":
        path = DEFAULT_LOG_PATH if os.path.exists(DEFAULT_LOG_PATH) else "app.log"

    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows


def p95(v):
    v = sorted(v)
    return v[max(int(len(v) * 0.95) - 1, 0)] if v else 0


def collect(path=None):
    rows = load(path)
    # Only analyze logs since the last service reset
    reset_indices = [i for i, r in enumerate(rows) if r.get("msg") == "service reset"]
    if reset_indices:
        rows = rows[reset_indices[-1]:]

    initial_version = rows[0].get("version", "v3.8.2") if rows else "v3.8.2"
    current_version = rows[-1].get("version", initial_version) if rows else initial_version
    deploy_events = [r for r in rows if r.get("msg") == "deploy complete"]
    version_changed = (current_version != initial_version) or any(r.get("version") == "v3.8.3" for r in deploy_events)

    lat_threshold = 120 if getattr(core, "FAST_MODE", False) else 400

    change_markers = [
        "config changed",
        "deploy complete",
        "database index dropped on orders(item)",
        "cache cluster disconnected, cache disabled",
        "memory leak simulation started",
        "downstream payment-gateway dependency degraded",
    ]
    changes = [r for r in rows if r.get("msg") in change_markers or (r.get("level") == "WARN" and "fix applied" not in r.get("msg", ""))]
    cut = changes[-1]["ts"] if changes else None

    err_rows = [r for r in rows if r.get("level") == "ERROR"]
    if not cut and err_rows:
        cut = err_rows[0]["ts"]

    ok = [r for r in rows if r.get("msg") == "checkout ok" and "latency_ms" in r]
    if not cut:
        high_lat = [r for r in ok if r.get("latency_ms", 0) > lat_threshold]
        if high_lat:
            cut = high_lat[0]["ts"]

    errs = [r for r in rows if r.get("level") == "ERROR" and (not cut or r["ts"] >= cut)]
    before = [r["latency_ms"] for r in ok if cut and r["ts"] < cut][-200:]
    after = [r["latency_ms"] for r in ok if not cut or r["ts"] >= cut]

    p95_before = p95(before)
    p95_after = p95(after) if after else (p95_before if not cut else 0)

    top_errors = Counter(r["msg"] for r in errs).most_common(3)
    recent_logs = [r.get("msg", "") for r in rows if cut and r["ts"] >= cut][-15:]
    recent_warnings = [r.get("msg", "") for r in rows if r.get("level") == "WARN" and (not cut or r["ts"] >= cut)][-10:]

    if any("memory 85% of limit" in m for m in recent_warnings + recent_logs):
        memory_trend = "critical"
    elif any("memory" in m for m in recent_warnings + recent_logs):
        memory_trend = "rising"
    else:
        memory_trend = "normal"

    if p95_after >= lat_threshold:
        latency_trend = "creeping" if memory_trend in ["critical", "rising"] else "high"
    else:
        latency_trend = "normal"

    if len(errs) > 0:
        symptom_type = "errors"
    elif memory_trend in ["critical", "rising"] or latency_trend == "creeping":
        symptom_type = "creeping-latency"
    elif p95_after >= lat_threshold:
        symptom_type = "latency-only"
    else:
        symptom_type = "normal"

    if top_errors:
        error_class = top_errors[0][0]
    elif any("slow query" in m or "index dropped" in m for m in recent_warnings + recent_logs):
        error_class = "slow query"
    elif any("cache" in m for m in recent_warnings + recent_logs):
        error_class = "cache disabled"
    elif any("memory" in m for m in recent_warnings + recent_logs):
        error_class = "memory 85% of limit"
    else:
        error_class = "none"

    incident_detected = bool(
        len(errs) > 0
        or p95_after >= lat_threshold
        or version_changed
        or memory_trend == "critical"
        or (cut and len(recent_warnings) > 0)
    )

    last_config_change = next((r for r in reversed(rows) if r.get("msg") == "config changed"), None)
    pool_size = errs[0].get("pool_size") if errs else (last_config_change.get("pool_size") if last_config_change else None)

    return {
        "incident_detected": incident_detected,
        "version": current_version,
        "version_changed": version_changed,
        "last_config_change": last_config_change,
        "last_deploy": deploy_events[-1] if deploy_events else None,
        "p95_before_ms": p95_before,
        "p95_after_ms": p95_after,
        "error_count": len(errs),
        "top_errors": top_errors,
        "pool_size": pool_size,
        "first_error_secs_after_change": round(errs[0]["ts"] - cut, 1) if errs and cut else None,
        "memory_trend": memory_trend,
        "latency_trend": latency_trend,
        "symptom_type": symptom_type,
        "error_class": error_class,
        "recent_logs": recent_logs,
        "recent_warnings": recent_warnings,
    }


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))
