import os
import time
import yaml
from typing import Dict, Any, List, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_POLICY_PATH = os.path.join(ROOT_DIR, "policies.yaml")

DEFAULT_POLICIES = {
    "slo_p95_ms": 500,
    "enable_cache": {"risk_tier": "low", "auto_apply_allowed": True, "min_confidence": 0.80, "max_auto_per_hour": 5},
    "restart_service": {"risk_tier": "low", "auto_apply_allowed": True, "min_confidence": 0.80, "max_auto_per_hour": 5},
    "rollback_config": {"risk_tier": "medium", "auto_apply_allowed": False, "min_confidence": 0.85, "max_auto_per_hour": 3},
    "rollback_deploy": {"risk_tier": "medium", "auto_apply_allowed": False, "min_confidence": 0.85, "max_auto_per_hour": 3},
    "scale_pool": {"risk_tier": "medium", "auto_apply_allowed": False, "min_confidence": 0.85, "max_auto_per_hour": 4},
    "enable_circuit_breaker": {"risk_tier": "medium", "auto_apply_allowed": False, "min_confidence": 0.85, "max_auto_per_hour": 3},
    "add_index": {"risk_tier": "high", "auto_apply_allowed": False, "min_confidence": 0.90, "max_auto_per_hour": 2},
    "codefix": {"risk_tier": "high", "auto_apply_allowed": False, "min_confidence": 0.90, "max_auto_per_hour": 2},
    "investigate": {"risk_tier": "low", "auto_apply_allowed": False, "min_confidence": 0.80, "max_auto_per_hour": 0},
}

_AUTO_FIX_TIMESTAMPS: List[float] = []


def load_policies(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or DEFAULT_POLICY_PATH
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    policies = dict(DEFAULT_POLICIES)
                    policies.update(loaded)
                    return policies
        except Exception as e:
            print(f"[policy] Warning: failed to parse {p}: {e}")
    return dict(DEFAULT_POLICIES)


def record_auto_applied_fix():
    """Record an auto-applied fix timestamp for rate limiting."""
    now = time.time()
    _AUTO_FIX_TIMESTAMPS.append(now)


def get_recent_auto_fixes_count(window_seconds: int = 3600) -> int:
    """Return the number of auto-applied fixes in the last hour."""
    now = time.time()
    cutoff = now - window_seconds
    # Clean up older timestamps
    global _AUTO_FIX_TIMESTAMPS
    _AUTO_FIX_TIMESTAMPS = [ts for ts in _AUTO_FIX_TIMESTAMPS if ts >= cutoff]
    return len(_AUTO_FIX_TIMESTAMPS)


def get_slo_p95_ms(policies_path: Optional[str] = None) -> float:
    """Return the configured SLO p95 threshold in milliseconds (default 500ms)."""
    policies = load_policies(policies_path)
    val = policies.get("slo_p95_ms", 500)
    try:
        return float(val)
    except Exception:
        return 500.0


def is_service_healthy(
    after: Dict[str, Any],
    before: Optional[Dict[str, Any]] = None,
    slo_p95_ms: Optional[float] = None,
) -> bool:
    """
    Judge recovery by absolute health:
    errors == 0 AND p95 < 500ms (configurable in policies.yaml as slo_p95_ms),
    or p95 improved vs before when within acceptable latency.
    """
    if slo_p95_ms is None:
        slo_p95_ms = get_slo_p95_ms()
    errors = after.get("errors", 0)
    p95 = after.get("p95_ms")
    if errors != 0 or p95 is None:
        return False
    # Absolute health: 0 errors and p95 within SLO threshold
    return p95 < slo_p95_ms


def evaluate_policy(
    action: str,
    confidence: float,
    evidence: Optional[Dict[str, Any]] = None,
    memory_matches: Optional[List[Dict[str, Any]]] = None,
    policies_path: Optional[str] = None,
    is_codefix: bool = False,
    source: Optional[str] = None,
    root_cause: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluates policy for a proposed remediation action.
    Returns:
        {
            "decision": "AUTO_APPLY" | "NEEDS_APPROVAL" | "BLOCKED",
            "action": action,
            "risk_tier": "low" | "medium" | "high",
            "reasons": list of human-readable reason strings,
            "min_confidence": float,
            "confidence": float,
            "worked_in_past": bool,
            "auto_fixes_last_hour": int,
            "max_auto_per_hour": int,
        }
    """
    if action == "investigate":
        src = source or (evidence.get("source") if evidence else None)
        if not src and root_cause and "Alert reported by " in root_cause:
            try:
                src = root_cause.split("Alert reported by ")[1].split(" could not")[0].strip()
            except Exception:
                src = None
        src = src or "external monitor"
        reason = f"Alert reported by {src} could not be corroborated by service telemetry"
        return {
            "decision": "NEEDS_APPROVAL",
            "action": "investigate",
            "risk_tier": "low",
            "reasons": [reason],
            "min_confidence": 0.80,
            "confidence": confidence,
            "worked_in_past": False,
            "auto_fixes_last_hour": get_recent_auto_fixes_count(),
            "max_auto_per_hour": 0,
        }
    policies = load_policies(policies_path)
    policy_key = "codefix" if (is_codefix or action == "codefix") else action
    policy = policies.get(policy_key, {"risk_tier": "high", "auto_apply_allowed": False, "min_confidence": 0.90, "max_auto_per_hour": 1})

    risk_tier = policy.get("risk_tier", "high").lower()
    auto_allowed = bool(policy.get("auto_apply_allowed", False))
    min_confidence = float(policy.get("min_confidence", 0.80))
    max_auto_per_hour = int(policy.get("max_auto_per_hour", 3))

    reasons: List[str] = []
    hourly_count = get_recent_auto_fixes_count()

    # Check whether memory shows this action worked before for this signature
    worked_in_past = False
    if memory_matches:
        worked_in_past = any(
            m.get("action") == action and m.get("worked", False)
            for m in memory_matches
        )

    # 1. High risk always needs approval
    if risk_tier == "high":
        reasons.append(f"Action '{action}' is classified as High Risk; mandatory human approval required by safety policy.")
        return {
            "decision": "NEEDS_APPROVAL",
            "action": action,
            "risk_tier": risk_tier,
            "reasons": reasons,
            "min_confidence": min_confidence,
            "confidence": confidence,
            "worked_in_past": worked_in_past,
            "auto_fixes_last_hour": hourly_count,
            "max_auto_per_hour": max_auto_per_hour,
        }

    # 2. Medium risk requires approval
    if risk_tier == "medium":
        reasons.append(f"Action '{action}' has Medium Risk tier; human operator approval required.")
        return {
            "decision": "NEEDS_APPROVAL",
            "action": action,
            "risk_tier": risk_tier,
            "reasons": reasons,
            "min_confidence": min_confidence,
            "confidence": confidence,
            "worked_in_past": worked_in_past,
            "auto_fixes_last_hour": hourly_count,
            "max_auto_per_hour": max_auto_per_hour,
        }

    # 3. Low risk checks
    # Check hourly quota
    if hourly_count >= max_auto_per_hour:
        reasons.append(
            f"Hourly auto-apply rate limit exceeded ({hourly_count}/{max_auto_per_hour} fixes in past hour). "
            f"Auto-apply blocked to prevent cascading changes."
        )
        return {
            "decision": "BLOCKED",
            "action": action,
            "risk_tier": risk_tier,
            "reasons": reasons,
            "min_confidence": min_confidence,
            "confidence": confidence,
            "worked_in_past": worked_in_past,
            "auto_fixes_last_hour": hourly_count,
            "max_auto_per_hour": max_auto_per_hour,
        }

    # Check confidence threshold
    if confidence < min_confidence:
        reasons.append(f"Confidence score {confidence:.2f} is below minimum auto-apply threshold {min_confidence:.2f}.")

    # Check memory evidence
    if not worked_in_past:
        reasons.append("Agent memory contains no prior successful resolution for this specific signature; human approval required before auto-applying.")

    if not auto_allowed:
        reasons.append("Auto-apply is disabled in policy configuration for this action.")

    if reasons:
        # One or more criteria failed
        return {
            "decision": "NEEDS_APPROVAL",
            "action": action,
            "risk_tier": risk_tier,
            "reasons": reasons,
            "min_confidence": min_confidence,
            "confidence": confidence,
            "worked_in_past": worked_in_past,
            "auto_fixes_last_hour": hourly_count,
            "max_auto_per_hour": max_auto_per_hour,
        }

    # All criteria passed for Low Risk: auto-apply!
    reasons.append(f"Action '{action}' is verified Low Risk.")
    reasons.append(f"Confidence {confidence:.2f} meets threshold {min_confidence:.2f}.")
    reasons.append("Agent memory confirms this action successfully resolved the exact same failure signature previously.")
    reasons.append(f"Hourly auto-apply budget satisfied ({hourly_count}/{max_auto_per_hour} used).")

    return {
        "decision": "AUTO_APPLY",
        "action": action,
        "risk_tier": risk_tier,
        "reasons": reasons,
        "min_confidence": min_confidence,
        "confidence": confidence,
        "worked_in_past": True,
        "auto_fixes_last_hour": hourly_count,
        "max_auto_per_hour": max_auto_per_hour,
    }
