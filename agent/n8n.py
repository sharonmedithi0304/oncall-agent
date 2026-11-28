import os
import json
import time
import hmac
import hashlib
import urllib.request
import threading
from typing import Dict, Any, Optional

def is_configured() -> bool:
    """Check if n8n webhook integration is configured."""
    return bool(os.environ.get("N8N_WEBHOOK_URL"))

def compute_hmac_signature(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for payload."""
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

def send_n8n_event(
    event_type: str,
    incident_id: str,
    data: Optional[Dict[str, Any]] = None,
    sync: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    POST a JSON event to N8N_WEBHOOK_URL with HMAC signature header.
    Key events: incident_opened, approval_needed, approved, applied, verified, rolled_back.
    Degrades gracefully when N8N_WEBHOOK_URL is not set.
    """
    webhook_url = os.environ.get("N8N_WEBHOOK_URL")
    if not webhook_url:
        return None

    secret = (
        os.environ.get("N8N_WEBHOOK_SECRET")
        or os.environ.get("WEBHOOK_SECRET")
        or "dev-secret"
    )

    payload = {
        "event": event_type,
        "incident_id": incident_id,
        "timestamp": time.time(),
        "data": data or {},
    }

    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    sig = compute_hmac_signature(payload_bytes, secret)

    headers = {
        "Content-Type": "application/json",
        "X-N8N-Signature": f"sha256={sig}",
        "X-Signature-SHA256": sig,
        "X-Hub-Signature-256": f"sha256={sig}",
    }

    def _post():
        try:
            req = urllib.request.Request(webhook_url, data=payload_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                try:
                    return json.loads(body)
                except Exception:
                    return {"status": "ok", "response": body}
        except Exception as e:
            print(f"[n8n] Warning sending event {event_type} to {webhook_url}: {e}")
            return {"error": str(e)}

    if sync:
        return _post()
    else:
        t = threading.Thread(target=_post, daemon=True)
        t.start()
        return {"status": "dispatched", "event": event_type, "signature": sig}
