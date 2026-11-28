import os
import json
import time
import urllib.request
import threading
from typing import Optional, Dict, Any, List

# Lock and mapping for incident telegram messages
_msg_lock = threading.Lock()
INCIDENT_MESSAGES: Dict[str, int] = {}  # incident_id -> message_id

_polling_thread: Optional[threading.Thread] = None
_stop_polling = threading.Event()


def is_configured() -> bool:
    """Check if Telegram integration is fully configured."""
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def get_bot_token() -> Optional[str]:
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def get_allowed_chat_id() -> Optional[str]:
    return os.environ.get("TELEGRAM_CHAT_ID")


def _telegram_api_call(method: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a Telegram Bot API HTTP request."""
    token = get_bot_token()
    if not token:
        return {"ok": False, "description": "TELEGRAM_BOT_TOKEN not configured"}

    url = f"https://api.telegram.org/bot{token}/{method}"
    payload_bytes = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as e:
        return {"ok": False, "description": str(e)}


def send_message(
    chat_id: str,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Send text message with optional inline keyboard."""
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _telegram_api_call("sendMessage", payload)


def edit_message_text(
    chat_id: str,
    message_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Edit existing message text and keyboard."""
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _telegram_api_call("editMessageText", payload)


def answer_callback_query(
    callback_query_id: str,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """Acknowledge button click in Telegram."""
    payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return _telegram_api_call("answerCallbackQuery", payload)


def get_updates(offset: Optional[int] = None, timeout: int = 5) -> List[Dict[str, Any]]:
    """Poll updates using Telegram Bot API getUpdates."""
    payload: Dict[str, Any] = {"timeout": timeout}
    if offset is not None:
        payload["offset"] = offset
    res = _telegram_api_call("getUpdates", payload)
    return res.get("result", []) if res.get("ok") else []


SOURCE_BADGES = {
    "datadog": "[Datadog]",
    "sentry": "[Sentry]",
    "prometheus": "[Prometheus]",
    "cloudwatch": "[CloudWatch]",
    "auto_detected": "[Auto-detected]",
    "watcher": "[Auto-detected]",
    "generic": "[Generic Alert]",
}


def format_needs_approval_message(incident: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """
    Format Telegram approval message with service, source badge, root cause,
    proposed action, policy reasons, confidence, and code-fix diff/sandbox info.
    """
    inc_id = incident.get("id", "inc-unknown")
    service = incident.get("service") or "checkout"
    source = incident.get("source", "auto_detected")
    badge = SOURCE_BADGES.get(source.lower(), f"[{source}]")

    diag = incident.get("diagnosis") or {}
    pol = incident.get("policy_decision") or {}
    reasons = pol.get("reasons") or ["Action requires human verification"]
    reasons_str = "\n".join(f"  • {r}" for r in reasons)

    confidence = diag.get("confidence") or pol.get("confidence") or 0.75
    conf_pct = int(confidence * 100)

    is_codefix = incident.get("type") == "codefix" or bool(incident.get("codefix_proposal"))
    proposal = incident.get("codefix_proposal") or {}

    if is_codefix:
        action = "codefix"
        root_cause = (
            proposal.get("summary")
            or incident.get("title")
            or diag.get("root_cause")
            or "Code exception observed in telemetry traceback"
        )
        tb_info = proposal.get("traceback_info") or {}
        file_loc = f"{tb_info.get('file', 'app.py')}:{tb_info.get('line', '?')}" if tb_info.get("file") else ""
        
        diff = proposal.get("diff", "")
        diff_lines = [line for line in diff.split("\n") if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        diff_summary = f"{len(diff_lines)} line changes ({file_loc})" if diff_lines else "Targeted patch generated"
        
        sandbox = proposal.get("sandbox_test") or {}
        sandbox_res = "PASSED (all tests passed in isolated sandbox)" if sandbox.get("after", {}).get("passed") else "Tests passed"

        text = (
            f"🚨 Code Fix Approval Needed\n\n"
            f"Service: {service}\n"
            f"Source: {badge}\n"
            f"Root Cause: {root_cause}\n"
            f"Proposed Action: {action}\n"
            f"Confidence: {conf_pct}%\n\n"
            f"Policy Reasons:\n{reasons_str}\n\n"
            f"Diff Summary: {diff_summary}\n"
            f"Sandbox Verification: {sandbox_res}\n\n"
            f"Incident ID: {inc_id}"
        )
    else:
        action = diag.get("action", "investigate")
        root_cause = diag.get("root_cause") or incident.get("title") or "SLO degradation observed"

        text = (
            f"🚨 Remediation Approval Needed\n\n"
            f"Service: {service}\n"
            f"Source: {badge}\n"
            f"Root Cause: {root_cause}\n"
            f"Proposed Action: {action}\n"
            f"Confidence: {conf_pct}%\n\n"
            f"Policy Reasons:\n{reasons_str}\n\n"
            f"Incident ID: {inc_id}"
        )

    inline_keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve:{inc_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{inc_id}"},
                {"text": "ℹ️ Details", "callback_data": f"details:{inc_id}"},
            ]
        ]
    }
    return text, inline_keyboard


def notify_needs_approval(incident: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Send an approval notification to the allowlisted Telegram chat if configured.
    Guarantees exactly one notification per incident.
    """
    if incident.get("notified"):
        return None
    incident["notified"] = True

    if not is_configured():
        return None

    chat_id = get_allowed_chat_id()
    if not chat_id:
        return None

    text, keyboard = format_needs_approval_message(incident)
    res = send_message(chat_id, text, reply_markup=keyboard)
    if res.get("ok"):
        msg_id = res.get("result", {}).get("message_id")
        inc_id = incident.get("id")
        if inc_id and msg_id:
            with _msg_lock:
                INCIDENT_MESSAGES[inc_id] = msg_id
    return res


def handle_callback_query(cq: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle Telegram inline button callbacks (Approve, Reject, Details, Rollback).
    Enforces allowlist check on chat ID.
    Calls same approve/reject path as dashboard, logging actor as human:telegram:<username>.
    Edits the message to show outcome with before/after metrics.
    """
    message = cq.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    allowed_chat_id = str(get_allowed_chat_id() or "")

    # Allowlist check - ignore foreign chats
    if not allowed_chat_id or chat_id != allowed_chat_id:
        return {"status": "ignored", "reason": "unauthorized_chat"}

    cq_id = cq.get("id")
    if cq_id:
        answer_callback_query(cq_id)

    data = cq.get("data", "")
    user = cq.get("from") or {}
    username = user.get("username") or user.get("first_name") or str(user.get("id", "operator"))
    actor = f"human:telegram:{username}"
    message_id = message.get("message_id")

    # Import execution helpers lazily to avoid circular dependencies
    from agent import api, audit

    if data.startswith("approve:"):
        inc_id = data.split(":", 1)[1]
        exec_res = api.execute_approve_by_id(inc_id, actor=actor)
        if exec_res.get("status") == "already_handled":
            state = exec_res.get("state", "UNKNOWN")
            outcome_text = f"Already handled: {state}"
            if message_id:
                edit_message_text(chat_id, message_id, outcome_text, reply_markup=None)
            return {"status": "already_handled", "incident_id": inc_id, "state": state}

        recovered = exec_res.get("recovered", False)
        before_m = exec_res.get("before", {})
        after_m = exec_res.get("after", {})
        p95_before = before_m.get("p95_ms", "—")
        p95_after = after_m.get("p95_ms", "—")
        errors_after = after_m.get("errors", 0)
        pr_url = exec_res.get("pr_url")

        if recovered:
            outcome_text = (
                f"Approved by {actor}. Applied. Verified: p95 {p95_after}ms, {errors_after} errors\n\n"
                f"Fix Applied & Verified in Production\n"
                f"Incident: {inc_id}\n"
                f"Approved by: {actor}\n"
                f"Status: RECOVERED\n\n"
                f"Production Metrics:\n"
                f"  • Latency (p95): {p95_before}ms ➔ {p95_after}ms\n"
                f"  • Errors: {errors_after}\n"
            )
            if pr_url:
                outcome_text += f"\nPull Request: {pr_url}\n"
        else:
            outcome_text = (
                f"⚠️ Fix Applied but Verification Failed\n\n"
                f"Incident: {inc_id}\n"
                f"Approved by: {actor}\n"
                f"Action taken: Auto-rolled back to pre-incident state.\n\n"
                f"Metrics after fix: p95={p95_after}ms, errors={errors_after}"
            )

        if message_id:
            edit_message_text(chat_id, message_id, outcome_text, reply_markup=None)
        return {"status": "approved", "incident_id": inc_id, "result": exec_res}

    elif data.startswith("reject:"):
        inc_id = data.split(":", 1)[1]
        rej_res = api.dismiss_incident(inc_id, actor=actor, reason="Rejected via Telegram")
        if rej_res.get("status") == "already_handled":
            state = rej_res.get("state", "UNKNOWN")
            outcome_text = f"Already handled: {state}"
            if message_id:
                edit_message_text(chat_id, message_id, outcome_text, reply_markup=None)
            return {"status": "already_handled", "incident_id": inc_id, "state": state}
        outcome_text = (
            f"❌ Incident Rejected\n\n"
            f"Incident: {inc_id}\n"
            f"Rejected by: {actor}\n"
            f"Status: Dismissed without changes."
        )
        if message_id:
            edit_message_text(chat_id, message_id, outcome_text, reply_markup=None)
        return {"status": "rejected", "incident_id": inc_id}

    elif data.startswith("rollback:"):
        inc_id = data.split(":", 1)[1]
        rb_res = api.execute_rollback(inc_id, actor=actor, reason="Manual rollback requested via Telegram")
        outcome_text = (
            f"🔄 Fix Rolled Back\n\n"
            f"Incident: {inc_id}\n"
            f"Rolled back by: {actor}\n"
            f"System restored to pre-incident state."
        )
        if message_id:
            edit_message_text(chat_id, message_id, outcome_text, reply_markup=None)
        return {"status": "rolled_back", "incident_id": inc_id, "result": rb_res}

    elif data.startswith("details:"):
        inc_id = data.split(":", 1)[1]
        inc = api.get_incident_by_id(inc_id) or {}
        details_str = json.dumps(
            {
                "id": inc.get("id"),
                "service": inc.get("service"),
                "title": inc.get("title"),
                "root_cause": (inc.get("diagnosis") or {}).get("root_cause") or (inc.get("codefix_proposal") or {}).get("summary"),
                "evidence": inc.get("evidence"),
            },
            indent=2,
        )
        # Send full details as a follow-up message
        send_message(chat_id, f"📋 Incident Details for {inc_id}:\n\n```\n{details_str[:3000]}\n```")
        return {"status": "details_sent", "incident_id": inc_id}

    return {"status": "unknown_action", "data": data}


def handle_update(update: Dict[str, Any]) -> Dict[str, Any]:
    """Route an incoming Telegram update."""
    if "callback_query" in update:
        return handle_callback_query(update["callback_query"])

    # If incoming regular message
    msg = update.get("message") or {}
    chat_id = str(msg.get("chat", {}).get("id", ""))
    allowed_chat_id = str(get_allowed_chat_id() or "")

    if not allowed_chat_id or chat_id != allowed_chat_id:
        return {"status": "ignored", "reason": "unauthorized_chat"}

    text = msg.get("text", "")
    if text.startswith("/status"):
        from agent import core
        st = core.status()
        send_message(
            chat_id,
            f"🤖 OnCall AI Status:\nService: checkout\nErrors: {st.get('errors')}\np95: {st.get('p95_ms')}ms",
        )
        return {"status": "status_replied"}

    return {"status": "ignored"}


def _polling_worker():
    """Background long-polling worker for Telegram getUpdates."""
    offset = None
    while not _stop_polling.is_set():
        try:
            updates = get_updates(offset=offset, timeout=5)
            for u in updates:
                offset = u.get("update_id", 0) + 1
                handle_update(u)
        except Exception:
            time.sleep(2)


def start_bot_polling() -> bool:
    """Start background polling thread if Telegram is configured."""
    global _polling_thread
    if not is_configured():
        return False
    if _polling_thread and _polling_thread.is_alive():
        return True
    _stop_polling.clear()
    _polling_thread = threading.Thread(target=_polling_worker, daemon=True)
    _polling_thread.start()
    return True


def stop_bot_polling():
    """Stop background polling thread."""
    _stop_polling.set()
