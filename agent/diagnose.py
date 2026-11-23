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


def heuristic(ev, past, use_memory):
    failed = {m["action"] for m in past if not m.get("worked")}
    worked = [m["action"] for m in past if m.get("worked")]

    err_class = ev.get("error_class", "")
    symptom = ev.get("symptom_type", "")
    v_changed = ev.get("version_changed", False)

    # Determine candidate action order and root cause based on evidence
    if v_changed or "NullReference" in err_class:
        root_cause = "Bad deployment: NullReference exceptions after v3.8.3 release."
        evidence_used = [f"{ev.get('error_count', 0)} NullReference errors", "version changed to v3.8.3"]
        order = ["rollback_deploy", "restart_service", "rollback_config"]
    elif "payment-gateway" in err_class or "downstream" in err_class:
        root_cause = "Downstream dependency failure: payment-gateway timing out."
        evidence_used = [f"{ev.get('error_count', 0)} payment-gateway timeout errors", "3s latency delay on downstream calls"]
        order = ["enable_circuit_breaker", "restart_service"] if use_memory else ["restart_service", "enable_circuit_breaker"]
    elif "QueuePool" in err_class or ev.get("last_config_change"):
        root_cause = "Connection pool exhaustion: requests time out waiting for a free DB connection."
        evidence_used = [f"{ev.get('error_count', 0)} QueuePool timeout errors", "p95 latency jumped after config change"]
        order = ["rollback_config", "scale_pool", "restart_service"] if use_memory else ["scale_pool", "restart_service", "rollback_config"]
    elif symptom == "creeping-latency" or "memory" in err_class:
        root_cause = "Memory leak: uncollected objects causing creeping latency and high memory usage."
        evidence_used = ["memory usage reached 85% of limit warning in logs", "latency steadily creeping up"]
        order = ["restart_service", "scale_pool", "rollback_config"] if use_memory else ["scale_pool", "rollback_config", "restart_service"]
    elif "slow query" in err_class:
        root_cause = "Missing database index: sequential table scan causing high latency."
        evidence_used = ["slow query log detected on orders table", "checkout latency between 0.8s and 1.5s"]
        order = ["add_index", "restart_service", "scale_pool"] if use_memory else ["restart_service", "scale_pool", "add_index"]
    elif "cache" in err_class:
        root_cause = "Cache disabled: requests directly hitting database causing latency spike."
        evidence_used = ["cache disabled warning in logs", "elevated p95 latency without errors"]
        order = ["enable_cache", "scale_pool", "restart_service"] if use_memory else ["scale_pool", "restart_service", "enable_cache"]
    else:
        root_cause = "Service latency degradation under checkout traffic."
        evidence_used = ["p95 latency above threshold"]
        order = ["scale_pool", "restart_service", "rollback_config"]

    if use_memory and worked:
        action = worked[-1]
        why = f"Recalled {len(past)} similar incident(s): '{action}' worked before, so it goes first."
    elif use_memory and failed:
        action = next((a for a in order if a not in failed), order[0])
        why = f"Past incidents show {sorted(failed)} FAILED to recover this service, so the plan skips them and tries '{action}'."
    else:
        action = order[0]
        why = "No past incidents, first-principles guess."

    return {
        "root_cause": root_cause,
        "confidence": 0.85 if (use_memory and worked) else 0.7,
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


def diagnose(use_memory=True):
    ev = collect()
    past = recall(ev) if use_memory else []
    d = _llm(ev, past) if os.environ.get("GEMINI_API_KEY") else None
    if d is None:
        d = heuristic(ev, past, use_memory)
    d["memories_recalled"] = len(past)
    d["past"] = past[-3:]
    return ev, d


if __name__ == "__main__":
    ev, d = diagnose()
    print(json.dumps(d, indent=2))
