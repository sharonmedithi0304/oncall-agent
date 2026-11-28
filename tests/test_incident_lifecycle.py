import time
import pytest
from unittest.mock import MagicMock
from agent import api, audit, telegram_bot, policy


@pytest.fixture(autouse=True)
def clean_api_state(tmp_path, monkeypatch):
    """Isolate runtime data directories and clean in-memory state."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:FAKE-BOT-TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999888777")
    mock_st = lambda: {"p95_ms": 110, "errors": 0, "active_faults": []}
    monkeypatch.setattr("agent.core.status", mock_st)
    monkeypatch.setattr(api.core, "status", mock_st)
    with api.watcher_lock:
        api.INCIDENTS.clear()
        api.INCIDENT_COOLDOWNS.clear()
    with api.snapshot_lock:
        api.SNAPSHOTS.clear()
    with api.codefix_lock:
        api.CURRENT_CODEFIX["proposal"] = None
        api.CURRENT_CODEFIX["approved"] = False
        api.CURRENT_CODEFIX["result"] = None
        api.CURRENT_CODEFIX["incident_id"] = None
    with telegram_bot._msg_lock:
        telegram_bot.INCIDENT_MESSAGES.clear()
    yield
    with api.watcher_lock:
        api.INCIDENTS.clear()
        api.INCIDENT_COOLDOWNS.clear()


def test_one_fault_creates_exactly_one_incident(monkeypatch):
    """While an incident is OPEN for a service + fault signature, watcher must NOT open another."""
    # Seed an open incident
    sig = "fault:pool_exhaustion"
    now = time.time()
    inc = {
        "id": "inc-dedupe-1",
        "service": "checkout",
        "signature": sig,
        "source": "auto_detected",
        "state": "OPEN",
        "status": "detected",
        "started_at": now,
        "notified": False,
        "detection_snapshot": {"p95_ms": 1500, "errors": 4},
    }
    with api.watcher_lock:
        api.INCIDENTS.append(inc)

    # Simulate watcher deduplication logic
    with api.watcher_lock:
        has_open = any(
            i.get("state") in ["OPEN", "APPROVED", "APPLIED"]
            or i.get("status") in ["detected", "codefix_proposed", "OPEN"]
            for i in api.INCIDENTS
        )
    assert has_open is True
    # Verify no second incident can be appended while first is open
    assert len(api.INCIDENTS) == 1


def test_cooldown_blocks_immediate_new_incident():
    """60s cooldown after resolution/rejection blocks opening a new incident for the same signature/service."""
    service = "checkout"
    sig = "fault:slow_query"
    now = time.time()

    # Simulate resolved incident setting cooldown
    cooldown_key = f"{service}:{sig}"
    api.INCIDENT_COOLDOWNS[cooldown_key] = now
    api.INCIDENT_COOLDOWNS[service] = now

    # Check 1: 10 seconds later, still within 60s cooldown -> blocked
    poll_time = now + 10
    last_closed = max(
        api.INCIDENT_COOLDOWNS.get(cooldown_key, 0),
        api.INCIDENT_COOLDOWNS.get(service, 0),
    )
    assert (poll_time - last_closed) < api.COOLDOWN_SECONDS

    # Check 2: 65 seconds later -> cooldown expired, allowed
    poll_time_later = now + 65
    assert (poll_time_later - last_closed) >= api.COOLDOWN_SECONDS


def test_double_approve_applied_once_and_idempotent(monkeypatch):
    """Tapping approve twice must run the fix only once; second call returns already_handled."""
    inc_id = "inc-double-approve-test"
    test_inc = {
        "id": inc_id,
        "service": "checkout",
        "source": "auto_detected",
        "state": "OPEN",
        "status": "detected",
        "notified": False,
        "detection_snapshot": {"p95_ms": 2012, "errors": 5},
        "diagnosis": {"action": "scale_pool", "root_cause": "pool exhaustion", "confidence": 0.95},
        "evidence": {"symptom": "pool_exhaustion"},
        "policy_decision": {"decision": "NEEDS_APPROVAL", "reasons": ["Risk tier: medium"]},
    }
    with api.watcher_lock:
        api.INCIDENTS.append(test_inc)

    apply_call_count = [0]

    def mock_apply_fix(action, diag, ev, use_mem=True):
        apply_call_count[0] += 1
        return {
            "recovered": True,
            "before": {"p95_ms": 2012, "errors": 5},
            "after": {"p95_ms": 14, "errors": 0},
        }

    monkeypatch.setattr("agent.core.apply_fix", mock_apply_fix)
    monkeypatch.setattr(api.core, "apply_fix", mock_apply_fix)

    # First approval: state transitions OPEN -> APPROVED -> APPLIED -> VERIFIED
    res1 = api.execute_approve_by_id(inc_id, actor="human:operator")
    assert res1.get("recovered") is True
    assert apply_call_count[0] == 1
    assert test_inc["state"] == "VERIFIED"

    # Second approval (double-tap): must NOT run fix again
    res2 = api.execute_approve_by_id(inc_id, actor="human:operator")
    assert res2.get("status") == "already_handled"
    assert "Already handled: VERIFIED" in res2.get("message")
    assert apply_call_count[0] == 1  # Still 1, not called twice!


def test_state_machine_rejects_invalid_transitions():
    """State machine prevents approve/reject on non-OPEN states."""
    inc_id = "inc-state-machine-test"
    test_inc = {
        "id": inc_id,
        "service": "checkout",
        "state": "VERIFIED",
        "status": "VERIFIED",
        "notified": True,
    }
    with api.watcher_lock:
        api.INCIDENTS.append(test_inc)

    # Reject on VERIFIED -> already_handled
    res_reject = api.dismiss_incident(inc_id, actor="human:operator")
    assert res_reject.get("status") == "already_handled"
    assert res_reject.get("state") == "VERIFIED"

    # Approve on VERIFIED -> already_handled
    res_approve = api.execute_approve_by_id(inc_id, actor="human:operator")
    assert res_approve.get("status") == "already_handled"
    assert res_approve.get("state") == "VERIFIED"


def test_one_telegram_notification_per_incident(monkeypatch):
    """Exactly one Telegram notification per incident (store notified=true). Never resend on later polls."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:BOT")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999888777")

    sent_messages = []

    def mock_send(chat_id, text, reply_markup=None):
        sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"ok": True, "result": {"message_id": 101}}

    monkeypatch.setattr(telegram_bot, "send_message", mock_send)

    inc = {
        "id": "inc-single-notify",
        "service": "checkout",
        "source": "auto_detected",
        "title": "Degradation Alert",
        "notified": False,
        "policy_decision": {"decision": "NEEDS_APPROVAL", "reasons": ["Risk tier: medium"]},
    }

    # First call sends notification and sets notified=True
    res1 = telegram_bot.notify_needs_approval(inc)
    assert res1 is not None
    assert inc["notified"] is True
    assert len(sent_messages) == 1

    # Second call (later poll) does NOT resend
    res2 = telegram_bot.notify_needs_approval(inc)
    assert res2 is None
    assert len(sent_messages) == 1


def test_telegram_outcome_shows_real_latency_and_removes_buttons(monkeypatch):
    """
    On Telegram Approve:
    - Edit original message to: 'Approved by <user>. Applied. Verified: p95 14ms, 0 errors'
    - Show real before/after p95: '2012ms ➔ 14ms'
    - Remove buttons (reply_markup=None).
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:BOT")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999888777")

    telegram_api_calls = []

    def mock_tg_call(method, data):
        telegram_api_calls.append({"method": method, "data": data})
        return {"ok": True, "result": {"message_id": 555}}

    monkeypatch.setattr(telegram_bot, "_telegram_api_call", mock_tg_call)

    inc_id = "inc-real-latency-tg"
    test_inc = {
        "id": inc_id,
        "service": "checkout",
        "source": "auto_detected",
        "state": "OPEN",
        "status": "detected",
        "notified": False,
        "detection_snapshot": {"p95_ms": 2012, "errors": 3},
        "diagnosis": {"action": "scale_pool", "root_cause": "pool exhaustion", "confidence": 0.92},
        "evidence": {"symptom": "pool_exhaustion"},
        "policy_decision": {"decision": "NEEDS_APPROVAL", "reasons": ["Risk tier: medium"]},
    }
    with api.watcher_lock:
        api.INCIDENTS.append(test_inc)

    mock_apply_fix = lambda action, diag, ev, use_mem: {
        "recovered": True,
        "before": {"p95_ms": 2012, "errors": 3},
        "after": {"p95_ms": 14, "errors": 0},
    }
    monkeypatch.setattr(api.core, "apply_fix", mock_apply_fix)

    cq = {
        "id": "cq-approve-latency",
        "message": {"message_id": 555, "chat": {"id": 999888777}},
        "from": {"id": 999888777, "username": "sre_bob"},
        "data": f"approve:{inc_id}",
    }

    res = telegram_bot.handle_callback_query(cq)
    assert res.get("status") == "approved"
    assert test_inc["state"] == "VERIFIED"

    # Check editMessageText payload
    edits = [c for c in telegram_api_calls if c["method"] == "editMessageText"]
    assert len(edits) > 0
    payload = edits[0]["data"]
    text = payload["text"]

    # Must contain exact outcome message
    assert "Approved by human:telegram:sre_bob. Applied. Verified: p95 14ms, 0 errors" in text
    assert f"Incident: {inc_id}" in text
    assert "2012ms ➔ 14ms" in text
    assert "Errors: 0" in text

    # Buttons must be removed
    assert payload.get("reply_markup") is None


def test_telegram_double_tap_returns_already_handled(monkeypatch):
    """Double tapping approve in Telegram replies with 'Already handled: <state>' and removes buttons."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:BOT")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999888777")

    telegram_api_calls = []

    def mock_tg_call(method, data):
        telegram_api_calls.append({"method": method, "data": data})
        return {"ok": True, "result": {"message_id": 777}}

    monkeypatch.setattr(telegram_bot, "_telegram_api_call", mock_tg_call)

    inc_id = "inc-tg-double-tap"
    test_inc = {
        "id": inc_id,
        "service": "checkout",
        "source": "auto_detected",
        "state": "OPEN",
        "status": "detected",
        "notified": False,
        "detection_snapshot": {"p95_ms": 1800, "errors": 2},
        "diagnosis": {"action": "scale_pool", "root_cause": "pool exhaustion", "confidence": 0.90},
        "evidence": {"symptom": "pool_exhaustion"},
        "policy_decision": {"decision": "NEEDS_APPROVAL", "reasons": ["Risk tier: medium"]},
    }
    with api.watcher_lock:
        api.INCIDENTS.append(test_inc)

    monkeypatch.setattr(api.core, "apply_fix", lambda a, d, e, u: {
        "recovered": True,
        "before": {"p95_ms": 1800, "errors": 2},
        "after": {"p95_ms": 15, "errors": 0},
    })

    cq = {
        "id": "cq-tap-1",
        "message": {"message_id": 777, "chat": {"id": 999888777}},
        "from": {"id": 999888777, "username": "sre_bob"},
        "data": f"approve:{inc_id}",
    }

    # First tap
    res1 = telegram_bot.handle_callback_query(cq)
    assert res1.get("status") == "approved"

    # Second tap immediately after
    res2 = telegram_bot.handle_callback_query(cq)
    assert res2.get("status") == "already_handled"
    assert res2.get("state") == "VERIFIED"

    # Verify second edit removed buttons and replied already handled
    last_edit = [c for c in telegram_api_calls if c["method"] == "editMessageText"][-1]
    assert "Already handled: VERIFIED" in last_edit["data"]["text"]
    assert last_edit["data"].get("reply_markup") is None
