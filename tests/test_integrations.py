import os
import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock
from io import BytesIO
import pytest

from agent import pr_body, codefix, telegram_bot, n8n, audit, api


def test_pr_body_contains_all_sections():
    """Verify generated PR body contains all 10 required sections."""
    proposal = {
        "incident_id": "inc-test1234",
        "summary": "Fix KeyError in discount calculation",
        "diff": "--- a/app.py\n+++ b/app.py\n@@ -10,3 +10,3 @@\n- discount = cart['discount']\n+ discount = cart.get('discount', 0)",
        "traceback_info": {
            "file": "demo_app/checkout.py",
            "line": 142,
            "exception": "KeyError",
            "message": "'discount'",
        },
        "sandbox_test": {
            "before": {"passed": False, "output": "FAILED test_checkout.py::test_discount - KeyError: 'discount'"},
            "after": {"passed": True, "output": "PASSED test_checkout.py::test_discount"},
        },
        "verification_metrics": {
            "before": {"p95_ms": 1250, "errors": 42},
            "after": {"p95_ms": 115, "errors": 0},
        },
        "policy_decision": {
            "decision": "NEEDS_APPROVAL",
            "risk_tier": "high",
            "reasons": ["Code changes require human verification (tier: high)"],
        },
    }

    body = pr_body.generate_pr_body(proposal, incident_id="inc-test1234")

    # 1. Summary
    assert "## Summary" in body
    assert "KeyError in discount calculation" in body

    # 2. Root Cause
    assert "## Root Cause" in body
    assert "KeyError" in body

    # 3. Stack Trace Location
    assert "## Stack Trace Location" in body
    assert "demo_app/checkout.py" in body

    # 4. Unified Diff Summary
    assert "## Unified Diff Summary" in body
    assert "cart.get('discount', 0)" in body

    # 5. Sandbox Test Results before/after
    assert "## Sandbox Test Results" in body
    assert "FAILED" in body
    assert "PASSED" in body

    # 6. Production Verification Metrics before/after
    assert "## Production Verification Metrics" in body
    assert "1250ms" in body
    assert "115ms" in body

    # 7. Policy Decision
    assert "## Policy Decision" in body
    assert "NEEDS_APPROVAL" in body
    assert "high" in body

    # 8. Incident Report
    assert "## Incident Report" in body
    assert "inc-test1234" in body

    # 9. Audit Chain Head Hash
    assert "## Audit Chain Head Hash" in body

    # 10. Rollback Instructions
    assert "## Rollback Instructions" in body
    assert "git revert" in body or "/api/rollback" in body


def test_github_api_called_with_correct_payload(monkeypatch):
    """Verify GitHub API is called with correct payload, branch, headers, and labels."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_mock_token_12345")

    captured_requests = []

    def mock_urlopen(req, timeout=15):
        captured_requests.append(req)
        # Return mock PR response matching the expected URL
        resp_data = {
            "html_url": "https://github.com/my-org/checkout-service/pull/42",
            "number": 42,
            "title": req.headers.get("Title", "Fix"),
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
    monkeypatch.setattr("agent.codefix.get_git_remote_url", lambda: "https://github.com/my-org/checkout-service.git")

    # Mock git commands to avoid real filesystem git changes or network push
    push_called = []
    def mock_run_git_cmd(args):
        if "push" in args:
            push_called.append(" ".join(args))
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "pushed"
        return mock_proc

    monkeypatch.setattr("agent.codefix._run_git_cmd", mock_run_git_cmd)

    proposal = {
        "incident_id": "inc-gh-test",
        "severity": "critical",
        "summary": "Fix KeyError in cart calculation",
        "diff": "+ new code",
    }

    res = codefix.handle_git_pr(
        branch="fix/inc-gh-test",
        proposal=proposal,
        commit_msg="fix(checkout): resolve cart KeyError",
        pr_title="fix: automated remediation for inc-gh-test",
    )

    assert res["opened"] is True
    assert res["pr_url"] == "https://github.com/my-org/checkout-service/pull/42"
    assert len(push_called) > 0
    # Ensure it never pushes directly to main
    assert "main" not in push_called[0].split()[-1]

    # Verify captured GitHub API request
    assert len(captured_requests) >= 1
    pr_req = captured_requests[0]
    assert "api.github.com/repos/my-org/checkout-service/pulls" in pr_req.full_url
    assert pr_req.headers.get("Authorization") == "Bearer ghp_mock_token_12345"

    body = json.loads(pr_req.data.decode("utf-8"))
    assert body["head"] == "fix/inc-gh-test"
    assert body["base"] == "main"
    assert "automated remediation" in body["title"]
    assert "## Summary" in body["body"]


def test_telegram_ignores_non_allowlisted_chat_id(monkeypatch):
    """Verify Telegram bot strictly ignores updates from non-allowlisted chat IDs."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "100200300")

    # Message from foreign chat
    foreign_message_update = {
        "update_id": 1,
        "message": {
            "message_id": 99,
            "chat": {"id": 999999999},  # Unauthorized chat
            "text": "/status",
        },
    }

    res1 = telegram_bot.handle_update(foreign_message_update)
    assert res1.get("status") == "ignored"
    assert res1.get("reason") == "unauthorized_chat"

    # Callback query from foreign chat
    foreign_cq_update = {
        "update_id": 2,
        "callback_query": {
            "id": "cq-123",
            "message": {
                "message_id": 100,
                "chat": {"id": 888888888},  # Unauthorized chat
            },
            "from": {"id": 888, "username": "attacker"},
            "data": "approve:inc-fake",
        },
    }

    res2 = telegram_bot.handle_update(foreign_cq_update)
    assert res2.get("status") == "ignored"
    assert res2.get("reason") == "unauthorized_chat"


def test_telegram_approve_callback_triggers_approval_and_audit(monkeypatch):
    """Verify Telegram approve button invokes approval flow, records audit with human:telegram:<user>, and edits message."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "100200300")

    telegram_api_calls = []

    def mock_telegram_api(method, data):
        telegram_api_calls.append({"method": method, "data": data})
        return {"ok": True, "result": {"message_id": 4242}}

    monkeypatch.setattr("agent.telegram_bot._telegram_api_call", mock_telegram_api)

    # Seed an incident in INCIDENTS
    inc_id = "inc-tg-approval-test"
    test_inc = {
        "id": inc_id,
        "service": "checkout",
        "source": "datadog",
        "severity": "critical",
        "title": "Pool Starvation",
        "status": "detected",
        "diagnosis": {"action": "scale_pool", "root_cause": "Pool exhausted", "confidence": 0.88},
        "evidence": {"symptom": "pool_exhaustion"},
        "policy_decision": {"decision": "NEEDS_APPROVAL", "reasons": ["Risk tier: medium"]},
    }
    with api.watcher_lock:
        api.INCIDENTS.append(test_inc)

    # Mock core apply_fix and status to simulate successful recovery without real network sockets
    mock_status = lambda: {"p95_ms": 110, "errors": 0, "active_faults": []}
    mock_apply_fix = lambda action, diag, ev, use_mem: {
        "recovered": True,
        "before": {"errors": 5, "p95_ms": 650},
        "after": {"errors": 0, "p95_ms": 110},
    }
    monkeypatch.setattr("agent.core.status", mock_status)
    monkeypatch.setattr(api.core, "status", mock_status)
    monkeypatch.setattr("agent.core.apply_fix", mock_apply_fix)
    monkeypatch.setattr(api.core, "apply_fix", mock_apply_fix)
    import core as root_core
    monkeypatch.setattr(root_core, "status", mock_status)
    monkeypatch.setattr(root_core, "apply_fix", mock_apply_fix)

    cq = {
        "id": "cq-approve-1",
        "message": {
            "message_id": 4242,
            "chat": {"id": 100200300},
        },
        "from": {"id": 100200300, "username": "oncall_alice"},
        "data": f"approve:{inc_id}",
    }

    res = telegram_bot.handle_callback_query(cq)
    assert res.get("status") == "approved"
    assert res.get("incident_id") == inc_id

    # Verify audit event has human:telegram:oncall_alice actor
    audit_entries = audit.get_audit_log(incident_id=inc_id)
    approved_events = [e for e in audit_entries if e["event"] == "approved"]
    assert len(approved_events) > 0
    assert approved_events[0]["actor"] == "human:telegram:oncall_alice"

    # Verify Telegram edited the message to show outcome & rollback button
    edit_calls = [c for c in telegram_api_calls if c["method"] == "editMessageText"]
    assert len(edit_calls) > 0
    edit_text = edit_calls[0]["data"]["text"]
    assert "Fix Applied & Verified in Production" in edit_text
    assert "oncall_alice" in edit_text
    assert "RECOVERED" in edit_text

    # Rollback button must be present in edited reply markup
    markup = edit_calls[0]["data"].get("reply_markup", {})
    buttons = markup.get("inline_keyboard", [[]])[0]
    assert any("Rollback" in b.get("text", "") for b in buttons)


def test_n8n_event_is_signed(monkeypatch):
    """Verify n8n webhook dispatches events with valid HMAC signature header."""
    test_secret = "test-secret-n8n-xyz"
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.corp.internal/webhook/oncall")
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", test_secret)

    captured = []

    def mock_urlopen(req, timeout=10):
        captured.append(req)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "accepted"}'
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    res = n8n.send_n8n_event(
        event_type="incident_opened",
        incident_id="inc-n8n-99",
        data={"service": "checkout", "severity": "critical"},
        sync=True,
    )

    assert len(captured) == 1
    req = captured[0]
    assert req.full_url == "https://n8n.corp.internal/webhook/oncall"

    # Check signature headers
    sig_header = req.headers.get("X-n8n-signature") or req.headers.get("X-N8N-Signature")
    assert sig_header is not None
    assert sig_header.startswith("sha256=")
    actual_sig = sig_header.replace("sha256=", "")

    # Calculate expected HMAC
    expected_sig = hmac.new(test_secret.encode("utf-8"), req.data, hashlib.sha256).hexdigest()
    assert actual_sig == expected_sig

    # Check body
    body = json.loads(req.data.decode("utf-8"))
    assert body["event"] == "incident_opened"
    assert body["incident_id"] == "inc-n8n-99"
    assert body["data"]["service"] == "checkout"


def test_graceful_skipping_when_env_vars_missing(monkeypatch):
    """Verify all integrations degrade gracefully and skip cleanly when env vars are absent."""
    # Ensure all integration env vars are cleared
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)

    # 1. GitHub PR creation skips network push and returns compare/branch info cleanly
    proposal = {
        "incident_id": "inc-no-token",
        "summary": "Local fix without token",
        "diff": "+ dummy change",
    }
    git_res = codefix.handle_git_pr(
        branch="fix/inc-no-token",
        proposal=proposal,
        commit_msg="fix: local commit",
        pr_title="fix: local pr",
    )
    assert git_res["opened"] is False
    assert git_res.get("reason") == "GITHUB_TOKEN not configured"
    assert "body" in git_res

    # 2. Telegram Bot helpers return None / False without raising
    assert telegram_bot.is_configured() is False
    assert telegram_bot.notify_needs_approval({"id": "inc-test"}) is None
    assert telegram_bot.start_bot_polling() is False

    # 3. n8n webhook returns None without raising
    assert n8n.is_configured() is False
    assert n8n.send_n8n_event("incident_opened", "inc-test", sync=True) is None

    # 4. Status endpoint reports Not configured
    status = api.get_integrations()
    assert status["github"]["configured"] is False
    assert "Not configured" in status["github"]["status"]
    assert status["telegram"]["configured"] is False
    assert "Not configured" in status["telegram"]["status"]
    assert status["n8n"]["configured"] is False
    assert "Not configured" in status["n8n"]["status"]
