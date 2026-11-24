import json, os, time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = "memory.json"


def get_data_dir() -> str:
    d = os.environ.get("DATA_DIR", os.path.join(ROOT_DIR, "data"))
    os.makedirs(d, exist_ok=True)
    return d


def _get_path(path=None):
    if path:
        return path
    if "MEMORY_PATH" in os.environ:
        return os.environ["MEMORY_PATH"]
    return os.path.join(get_data_dir(), PATH)


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
    if ev.get("codefix") or ev.get("has_code_bug"):
        exc = ev.get("code_exception") or ev.get("exception") or "Exception"
        f = ev.get("code_file") or ev.get("file") or "checkout_logic.py"
        return f"codefix|{exc}|{os.path.basename(f)}"

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
