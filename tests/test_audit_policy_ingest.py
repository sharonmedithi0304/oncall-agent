import os
import sys
import json
import time
import shutil
import tempfile
import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
AGENT_DIR = os.path.join(ROOT_DIR, "agent")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

import ingest
import policy
import audit
import collect
from agent.api import app as agent_app
from fastapi.testclient import TestClient


# ==============================================================================
# 1. ADAPTER NORMALIZATION TESTS
# ==============================================================================

def test_datadog_adapter_normalizes_sample():
    sample_path = os.path.join(ROOT_DIR, "samples", "datadog.json")
    with open(sample_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    incident = ingest.normalize_datadog(payload)
    assert incident.source == "datadog"
    assert incident.service == "checkout"
    assert incident.severity == "critical"
    assert "p95 latency" in incident.title.lower()
    assert "prod-checkout-01" in incident.description
    assert any("service:checkout" in t for t in incident.tags)
    assert any("env:production" in t for t in incident.tags)
    assert incident.id.startswith("inc-dd-")
    assert incident.started_at > 0
    assert incident.raw == payload


def test_sentry_adapter_normalizes_sample():
    sample_path = os.path.join(ROOT_DIR, "samples", "sentry.json")
    with open(sample_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    incident = ingest.normalize_sentry(payload)
    assert incident.source == "sentry"
    assert incident.service == "checkout"
    assert incident.severity == "critical"
    assert "AttributeError" in incident.title
    assert "checkout_logic in apply_discount" in incident.description
    assert any("service:checkout" in t for t in incident.tags)
    assert incident.id.startswith("inc-sentry-")
    assert incident.started_at > 0
    assert incident.raw == payload


def test_prometheus_adapter_normalizes_sample():
    sample_path = os.path.join(ROOT_DIR, "samples", "prometheus.json")
    with open(sample_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    incident = ingest.normalize_prometheus(payload)
    assert incident.source == "prometheus"
    assert incident.service == "checkout"
    assert incident.severity == "critical"
    assert "Checkout API p95 latency spike" in incident.title
    assert "exceeding 500ms" in incident.description
    assert any("service:checkout" in t for t in incident.tags)
    assert incident.id.startswith("inc-prom-")
    assert incident.started_at > 0
    assert incident.raw == payload


def test_cloudwatch_adapter_normalizes_sns_json_string():
    sample_path = os.path.join(ROOT_DIR, "samples", "cloudwatch.json")
    with open(sample_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    # Note requirement: AWS CloudWatch alarm via SNS where Message is a JSON string
    assert isinstance(payload["Message"], str)

    incident = ingest.normalize_cloudwatch(payload)
    assert incident.source == "cloudwatch"
    assert incident.service == "checkout"
    assert incident.severity == "critical"
    assert incident.title == "CheckoutQueuePoolExhaustion"
    assert "connection pool exhausted" in incident.description.lower()
    assert any("ServiceName:checkout" in t for t in incident.tags)
    assert incident.id.startswith("inc-cw-")
    assert incident.started_at > 0
    assert incident.raw == payload


def test_generic_adapter_normalizes_sample():
    sample_path = os.path.join(ROOT_DIR, "samples", "generic.json")
    with open(sample_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    incident = ingest.normalize_generic(payload)
    assert incident.source == "generic"
    assert incident.service == "checkout"
    assert incident.severity == "critical"
    assert "payment gateway" in incident.title.lower()
    assert incident.id.startswith("inc-gen-")
    assert incident.raw == payload


# ==============================================================================
# 2. WEBHOOK SECRET AUTHENTICATION TESTS
# ==============================================================================

def test_webhook_secret_enforcement(monkeypatch):
    import agent.api as api_mod
    monkeypatch.setattr(api_mod.dg, "diagnose", lambda use_memory=True: ({"incident_detected": True}, {"action": "scale_pool", "confidence": 0.8, "root_cause": "test"}))

    client = TestClient(api_mod.app)

    # Missing header -> 401
    res_no_header = client.post("/api/ingest/generic", json={"title": "test", "service": "checkout"})
    assert res_no_header.status_code == 401
    assert "Invalid or missing X-Webhook-Secret" in res_no_header.text

    # Wrong secret -> 401
    res_wrong_secret = client.post(
        "/api/ingest/generic",
        json={"title": "test", "service": "checkout"},
        headers={"X-Webhook-Secret": "wrong-secret-token"},
    )
    assert res_wrong_secret.status_code == 401

    # Correct secret -> 200
    res_ok = client.post(
        "/api/ingest/generic",
        json={"title": "Valid alert", "service": "checkout", "severity": "warning"},
        headers={"X-Webhook-Secret": "dev-secret"},
    )
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["status"] == "ingested"
    assert data["incident"]["service"] == "checkout"


# ==============================================================================
# 3. POLICY DECISIONS FOR RISK TIERS
# ==============================================================================

def test_policy_decisions_for_each_risk_tier():
    # 1. High Risk action (add_index, codefix) -> ALWAYS NEEDS_APPROVAL
    high_pol = policy.evaluate_policy(
        action="add_index",
        confidence=0.99,
        memory_matches=[{"action": "add_index", "worked": True}],
    )
    assert high_pol["risk_tier"] == "high"
    assert high_pol["decision"] == "NEEDS_APPROVAL"
    assert any("High Risk" in r for r in high_pol["reasons"])

    codefix_pol = policy.evaluate_policy(
        action="codefix",
        confidence=0.99,
        is_codefix=True,
    )
    assert codefix_pol["risk_tier"] == "high"
    assert codefix_pol["decision"] == "NEEDS_APPROVAL"

    # 2. Medium Risk action (rollback_config, scale_pool, etc.) -> NEEDS_APPROVAL
    med_pol = policy.evaluate_policy(
        action="rollback_config",
        confidence=0.95,
        memory_matches=[{"action": "rollback_config", "worked": True}],
    )
    assert med_pol["risk_tier"] == "medium"
    assert med_pol["decision"] == "NEEDS_APPROVAL"
    assert any("Medium Risk" in r for r in med_pol["reasons"])

    # 3. Low Risk action (enable_cache, restart_service)
    # 3a. Confidence >= threshold AND memory shows worked -> AUTO_APPLY
    low_auto = policy.evaluate_policy(
        action="enable_cache",
        confidence=0.88,
        memory_matches=[{"action": "enable_cache", "worked": True}],
    )
    assert low_auto["risk_tier"] == "low"
    assert low_auto["decision"] == "AUTO_APPLY"

    # 3b. Confidence < threshold -> NEEDS_APPROVAL
    low_low_conf = policy.evaluate_policy(
        action="enable_cache",
        confidence=0.70,
        memory_matches=[{"action": "enable_cache", "worked": True}],
    )
    assert low_low_conf["risk_tier"] == "low"
    assert low_low_conf["decision"] == "NEEDS_APPROVAL"
    assert any("below minimum" in r for r in low_low_conf["reasons"])

    # 3c. No memory match -> NEEDS_APPROVAL
    low_no_mem = policy.evaluate_policy(
        action="enable_cache",
        confidence=0.88,
        memory_matches=[],
    )
    assert low_no_mem["risk_tier"] == "low"
    assert low_no_mem["decision"] == "NEEDS_APPROVAL"
    assert any("no prior successful resolution" in r.lower() for r in low_no_mem["reasons"])

    # 4. Hourly rate cap exceeded -> BLOCKED
    # Simulate hourly cap exceeded
    for _ in range(6):
        policy.record_auto_applied_fix()
    low_blocked = policy.evaluate_policy(
        action="enable_cache",
        confidence=0.90,
        memory_matches=[{"action": "enable_cache", "worked": True}],
    )
    assert low_blocked["decision"] == "BLOCKED"
    assert any("rate limit exceeded" in r.lower() for r in low_blocked["reasons"])


# ==============================================================================
# 4. AUDIT CHAIN INTEGRITY & TAMPER DETECTION
# ==============================================================================

def test_audit_chain_verifies_and_detects_tampering():
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
        temp_path = f.name

    try:
        # 1. Append valid sequence of events
        audit.record_audit_event("ingested", incident_id="inc-101", actor="webhook:datadog", details={"alert": "p95"}, log_path=temp_path)
        audit.record_audit_event("detected", incident_id="inc-101", actor="system:watcher", details={"p95": 600}, log_path=temp_path)
        audit.record_audit_event("diagnosed", incident_id="inc-101", actor="agent:diagnose", details={"action": "restart_service"}, log_path=temp_path)
        audit.record_audit_event("policy_decision", incident_id="inc-101", actor="policy:engine", details={"decision": "AUTO_APPLY"}, log_path=temp_path)
        audit.record_audit_event("applied", incident_id="inc-101", actor="policy:auto_apply", details={"action": "restart_service"}, log_path=temp_path)
        audit.record_audit_event("verified", incident_id="inc-101", actor="system:verification", details={"recovered": True}, log_path=temp_path)

        # Verify intact chain
        v_ok = audit.verify_audit_chain(temp_path)
        assert v_ok["verified"] is True
        assert v_ok["total_entries"] == 6
        assert v_ok["error"] is None

        # 2. Tamper with line 2 (detected event)
        with open(temp_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        tampered_entry = json.loads(lines[1])
        tampered_entry["actor"] = "malicious_hacker"
        lines[1] = json.dumps(tampered_entry) + "\n"

        with open(temp_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        # Verification must now fail
        v_bad = audit.verify_audit_chain(temp_path)
        assert v_bad["verified"] is False
        assert v_bad["tampered_index"] == 1
        assert "Tamper detected" in v_bad["error"] or "Chain broken" in v_bad["error"]

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# ==============================================================================
# 5. AUTO-ROLLBACK TRIGGER & MANUAL ROLLBACK
# ==============================================================================

def test_auto_rollback_triggers_when_fix_makes_things_worse():
    import agent.api as api_mod
    inc_id = "inc-test-rollback"

    # Setup snapshot for incident
    api_mod.take_snapshot(inc_id, "infrastructure")
    assert inc_id in api_mod.SNAPSHOTS

    # Trigger rollback
    res = api_mod.execute_rollback(
        incident_id=inc_id,
        actor="system:auto_rollback",
        reason="Verification failed: 5 errors, p95 850ms > 400ms before",
    )
    assert res["status"] == "rolled_back"
    assert res["incident_id"] == inc_id

    # Verify audit trail contains rolled_back event
    audit_entries = audit.get_audit_log(incident_id=inc_id)
    rollback_event = next((e for e in audit_entries if e["event"] == "rolled_back"), None)
    assert rollback_event is not None
    assert rollback_event["actor"] == "system:auto_rollback"
    assert "Verification failed" in rollback_event["details"]["reason"]


def test_manual_rollback_endpoint():
    client = TestClient(agent_app)
    inc_id = "inc-manual-test"

    import agent.api as api_mod
    api_mod.take_snapshot(inc_id, "infrastructure")

    res = client.post("/api/rollback", json={"incident_id": inc_id, "actor": "human:operator", "reason": "Operator manual revert"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rolled_back"
    assert data["incident_id"] == inc_id


# ==============================================================================
# 6. OTLP LOGS INGESTION
# ==============================================================================

def test_otlp_logs_ingestion_and_collect_integration():
    sample_otlp = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "checkout"}},
                        {"key": "version", "value": {"stringValue": "v3.8.2"}},
                    ]
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": str(int(time.time() * 1e9)),
                                "severityText": "WARN",
                                "body": {"stringValue": "database index dropped on orders(item)"},
                                "attributes": [
                                    {"key": "item", "value": {"stringValue": "laptop"}},
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }

    count = ingest.ingest_otlp_logs(sample_otlp)
    assert count == 1

    # Check collect loads from ingested logs
    rows = collect.load()
    dropped_index_logs = [r for r in rows if "database index dropped" in r.get("msg", "")]
    assert len(dropped_index_logs) > 0


# ==============================================================================
# 7. ALERT / TELEMETRY MISMATCH & FALSE ROLLBACK PREVENTION TESTS
# ==============================================================================

def test_alert_telemetry_mismatch_returns_investigate_and_needs_approval():
    """
    Issue 1: Alert/telemetry mismatch.
    If an external alert reports a problem but our collected evidence shows no degradation,
    do NOT propose a fix. Return action 'investigate', confidence <= 0.3,
    root_cause 'Alert reported by <source> could not be corroborated by service telemetry',
    and proposed_fix listing what to check next. Policy engine returns NEEDS_APPROVAL.
    """
    import diagnose as dg

    # Clean evidence with 0 errors, normal latency, no traceback
    clean_ev = {
        "incident_detected": False,
        "error_count": 0,
        "latency_trend": "normal",
        "symptom_type": "normal",
        "has_code_bug": False,
        "code_exception": None,
        "traceback": None,
        "top_errors": [],
    }

    diag = dg.heuristic(clean_ev, past=[], tried=None, external_source="datadog")
    assert diag["action"] == "investigate"
    assert diag["confidence"] <= 0.3
    assert diag["root_cause"] == "Alert reported by datadog could not be corroborated by service telemetry"
    assert "Verify monitoring probe" in diag["proposed_fix"] or "1." in diag["proposed_fix"]

    # Policy evaluation must return NEEDS_APPROVAL with that exact reason
    pol = policy.evaluate_policy(
        action=diag["action"],
        confidence=diag["confidence"],
        evidence=clean_ev,
        source="datadog",
        root_cause=diag["root_cause"],
    )
    assert pol["decision"] == "NEEDS_APPROVAL"
    assert any("Alert reported by datadog could not be corroborated by service telemetry" in r for r in pol["reasons"])


def test_zero_errors_and_p95_112ms_is_not_rolled_back():
    """
    Issue 2: False rollback prevention.
    Judge recovery by absolute health: errors == 0 AND p95 < 500ms (configurable in policies.yaml as slo_p95_ms),
    or p95 improved vs before. Only auto-rollback if the service is unhealthy by that definition.
    Proves that 0 errors / p95 112ms after a fix is NOT rolled back (even when p95_before was fast e.g. 20ms during 500 errors).
    """
    # Absolute health: 0 errors and p95 112ms is well below slo_p95_ms (500ms)
    before_metrics = {"errors": 5, "p95_ms": 20}
    after_metrics = {"errors": 0, "p95_ms": 112}

    healthy = policy.is_service_healthy(after_metrics, before_metrics, slo_p95_ms=500)
    assert healthy is True, "0 errors and p95 112ms (< 500ms) must be considered healthy"

    # Also test with default slo_p95_ms from policies.yaml
    assert policy.get_slo_p95_ms() == 500
    assert policy.is_service_healthy(after_metrics, before_metrics) is True

    # Unhealthy case: errors > 0
    unhealthy_errors = {"errors": 2, "p95_ms": 80}
    assert policy.is_service_healthy(unhealthy_errors, before_metrics) is False

    # Unhealthy case: p95 >= 500ms and not improved vs before
    unhealthy_latency = {"errors": 0, "p95_ms": 650}
    assert policy.is_service_healthy(unhealthy_latency, before_metrics) is False


def test_admin_reset_demo_data_endpoint():
    """
    Issue 3: POST /api/admin/reset-demo-data clears audit, memory, incidents and ingested logs
    (not benchmark results).
    """
    client = TestClient(agent_app)
    import memory

    # Populate some demo data
    audit.record_audit_event("ingested", incident_id="inc-reset-test")
    memory.remember({"signature": "test|sig", "action": "restart_service", "worked": True})
    ingest.ingest_otlp_logs([{"ts": time.time(), "level": "INFO", "msg": "test record"}])

    assert len(audit.get_audit_log()) > 0
    assert len(memory.load()) > 0
    assert len(ingest.get_ingested_logs()) > 0

    # Call reset endpoint
    res = client.post("/api/admin/reset-demo-data")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # Verify cleared
    assert len(audit.get_audit_log()) == 0
    assert len(memory.load()) == 0
    assert len(ingest.get_ingested_logs()) == 0
    assert len(client.get("/api/incidents").json()) == 0

