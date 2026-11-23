import json, os, time

PATH = "memory.json"


def load():
    if os.path.exists(PATH):
        try:
            with open(PATH) as f:
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


def recall(ev):
    sig = signature(ev)
    return [m for m in load() if m.get("signature") == sig]


def remember(entry):
    mem = load()
    entry["ts"] = time.time()
    mem.append(entry)
    with open(PATH, "w") as f:
        json.dump(mem, f, indent=2)


def clear():
    if os.path.exists(PATH):
        try:
            os.remove(PATH)
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
