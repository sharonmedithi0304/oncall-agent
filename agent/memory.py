import json, os, time

PATH = "memory.json"


def _get_path(path=None):
    return path or os.environ.get("MEMORY_PATH", PATH)


def load(path=None):
    p = _get_path(path)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def signature(ev):
    err_class = ev.get("error_class")
    if not err_class:
        top_errors = ev.get("top_errors") or []
        err_class = top_errors[0][0] if top_errors else "none"

    symptom = ev.get("symptom_type")
    if not symptom:
        if ev.get("error_count", 0) > 0:
            symptom = "errors"
        elif ev.get("memory_trend") == "critical" or ev.get("latency_trend") == "creeping":
            symptom = "creeping-latency"
        else:
            symptom = "latency-only"

    v_tag = "version_changed" if ev.get("version_changed") else "version_same"
    return f"{err_class}|{symptom}|{v_tag}"


def recall(ev, path=None):
    sig = signature(ev)
    return [m for m in load(path=path) if m.get("signature") == sig]


def remember(entry, path=None):
    p = _get_path(path)
    mem = load(path=p)
    entry["ts"] = time.time()
    mem.append(entry)
    parent = os.path.dirname(os.path.abspath(p))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2)


def clear(path=None):
    p = _get_path(path)
    if os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass


def as_prompt(past):
    if not past:
        return "No past incidents."
    return "\n".join(
        f"- cause: {m.get('root_cause')} | action: {m.get('action')} | "
        f"{'WORKED' if m.get('worked') else 'FAILED'} | p95 after: {m.get('after', {}).get('p95_ms')}ms"
        for m in past[-8:]
    )
