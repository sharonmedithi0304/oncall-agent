import os, json, time
from collect import collect
from memory import recall, as_prompt, signature

ACTIONS = [
    "rollback_config",
    "scale_pool",
    "restart_service",
    "add_index",
    "rollback_deploy",
    "enable_circuit_breaker",
    "enable_cache",
]

MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

PROMPT = """You are an on-call SRE agent. From the incident evidence and past incidents, return ONLY JSON:
{"root_cause": str, "confidence": number 0-1, "evidence_used": [str], "action": one of %s, "proposed_fix": str, "why_plan_changed": str}
Actions:
- rollback_config: revert config change when connection hold/timeout was modified (cures pool exhaustion)
- scale_pool: raise DB pool size
- restart_service: restart app to clear memory leak / flush state (cures memory leak)
- add_index: add DB index when slow table scans occur (cures slow query)
- rollback_deploy: rollback bad code deploy when version changed or null pointer exceptions occur (cures bad deploy)
- enable_circuit_breaker: enable circuit breaker for failing downstream timeouts (cures downstream timeout)
- enable_cache: enable cache when cache disabled causes direct DB latency (cures cache disabled)
Rules: never choose an action that FAILED in a past incident; prefer an action that WORKED. In why_plan_changed explain how past incidents changed your plan ("No past incidents, first-principles guess" if none).
""" % ACTIONS

FIX_DESCRIPTIONS = {
    "rollback_config": "Roll back the last config change (connections held too long).",
    "scale_pool": "Increase the DB connection pool size to handle concurrency.",
    "restart_service": "Restart the service process to purge memory leak and reset state.",
    "add_index": "Add missing database index on orders(item) for slow query resolution.",
    "rollback_deploy": "Roll back recent bad deployment to previous stable release (v3.8.2).",
    "enable_circuit_breaker": "Enable circuit breaker on payment-gateway to prevent cascading timeouts.",
    "enable_cache": "Enable cache layer to reduce direct database load.",
}


def mode():
    return "gemini" if os.environ.get("GEMINI_API_KEY") else "offline"


def heuristic(ev, past, tried=None):
    """
    Offline heuristic.
    First-principles guess is a fixed, plausible, evidence-based prior that is wrong for some faults.
    Memory is the ONLY difference: past incidents with the same signature are recalled to prefer
    an action that WORKED and exclude actions that FAILED.
    Within ONE incident, tried actions (session context) may also be excluded.
    No branching on use_memory occurs inside this heuristic.
    """
    failed_in_past = {m["action"] for m in past if not m.get("worked")}
    worked_in_past = [m["action"] for m in past if m.get("worked")]
    session_tried = set(tried or [])
    to_exclude = failed_in_past | session_tried

    err_class = ev.get("error_class", "")
    symptom = ev.get("symptom_type", "")
    v_changed = ev.get("version_changed", False)

    # Fixed, evidence-based prior orders (identical regardless of memory mode)
    if v_changed or "NullReference" in err_class:
        root_cause = "Bad deployment: NullReference exceptions after v3.8.3 release."
        evidence_used = [f"{ev.get('error_count', 0)} NullReference errors", "version changed to v3.8.3"]
        order = ["rollback_deploy", "restart_service", "rollback_config"]
    elif "payment-gateway" in err_class or "downstream" in err_class:
        root_cause = "Downstream dependency failure: payment-gateway timing out."
        evidence_used = [f"{ev.get('error_count', 0)} payment-gateway timeout errors", "downstream calls failing"]
        # Plausible first-principles guess: restart service to reset network connections
        order = ["restart_service", "scale_pool", "enable_circuit_breaker"]
    elif "QueuePool" in err_class or ev.get("last_config_change"):
        root_cause = "Connection pool exhaustion: requests time out waiting for a free DB connection."
        evidence_used = [f"{ev.get('error_count', 0)} QueuePool timeout errors", "connection timeout reached"]
        # Plausible first-principles guess: scale pool size
        order = ["scale_pool", "restart_service", "rollback_config"]
    elif symptom == "creeping-latency" or "memory" in err_class:
        root_cause = "Memory leak: uncollected objects causing creeping latency and high memory usage."
        evidence_used = ["memory usage high", "latency creeping up"]
        # Plausible first-principles guess: scale pool to alleviate latency pressure
        order = ["scale_pool", "rollback_config", "restart_service"]
    elif "slow query" in err_class:
        root_cause = "Missing database index: sequential table scan causing high latency."
        evidence_used = ["slow query detected", "latency-only symptom"]
        # Plausible first-principles guess: restart service to clear locks/caches
        order = ["restart_service", "scale_pool", "add_index"]
    elif "cache" in err_class:
        root_cause = "Cache disabled: requests directly hitting database causing latency spike."
        evidence_used = ["cache disabled warning in logs", "latency elevated without errors"]
        # Plausible first-principles guess: restart service
        order = ["restart_service", "scale_pool", "enable_cache"]
    else:
        root_cause = "Service latency degradation under checkout traffic."
        evidence_used = ["p95 latency above threshold"]
        order = ["restart_service", "scale_pool", "rollback_config"]

    # If memory recalled an action that worked and it hasn't failed in this session, prioritize it
    recalled_action = next((a for a in reversed(worked_in_past) if a not in session_tried), None)

    if recalled_action:
        action = recalled_action
        why = f"Recalled {len(past)} similar incident(s): '{action}' worked before, so it goes first."
    else:
        action = next((a for a in order if a not in to_exclude), order[0])
        if to_exclude:
            reasons = []
            if failed_in_past:
                reasons.append(f"past failures: {sorted(failed_in_past)}")
            if session_tried:
                reasons.append(f"tried this session: {sorted(session_tried)}")
            why = f"Skipping {', '.join(reasons)}; trying '{action}'."
        else:
            why = "No past incidents, first-principles guess."

    return {
        "root_cause": root_cause,
        "confidence": 0.85 if recalled_action else 0.7,
        "evidence_used": evidence_used,
        "action": action,
        "proposed_fix": FIX_DESCRIPTIONS.get(action, f"Apply fix: {action}"),
        "why_plan_changed": why,
        "model_used": "offline-heuristic",
    }


def _llm(ev, past):
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    text = PROMPT + "\nEvidence:\n" + json.dumps(ev, indent=2) + "\n\nPast similar incidents:\n" + as_prompt(past)
    for model in MODELS:
        for _ in range(2):
            try:
                r = client.models.generate_content(
                    model=model, contents=text,
                    config={"response_mime_type": "application/json"},
                )
                d = json.loads(r.text)
                if d.get("action") not in ACTIONS:
                    continue
                d["model_used"] = model
                return d
            except Exception as e:
                print(f"[{model}] failed: {str(e)[:80]}")
                time.sleep(1.5)
    return None


def diagnose(use_memory=True, tried_actions=None, memory_path=None):
    ev = collect()
    past = recall(ev, path=memory_path) if use_memory else []
    d = _llm(ev, past) if os.environ.get("GEMINI_API_KEY") else None
    if d is None:
        d = heuristic(ev, past, tried=tried_actions)
    d["memories_recalled"] = len(past)
    d["past"] = past[-3:]
    return ev, d


if __name__ == "__main__":
    ev, d = diagnose()
    print(json.dumps(d, indent=2))
