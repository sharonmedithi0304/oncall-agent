import os
import json
import time
import uuid
import yaml
from datetime import datetime
from typing import Dict, Any, List, Optional, Union

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_DIR = os.path.join(ROOT_DIR, "audit")
INGESTED_LOGS_PATH = os.path.join(AUDIT_DIR, "ingested_logs.jsonl")
SERVICES_PATH = os.path.join(ROOT_DIR, "services.yaml")


def load_services(path: Optional[str] = None) -> List[Dict[str, Any]]:
    p = path or SERVICES_PATH
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"[ingest] Warning loading {p}: {e}")
    return [
        {"name": "checkout", "owner": "checkout-sre", "repo": "demo_app", "risk_tier": "high"},
        {"name": "payment-gateway", "owner": "payments-team", "repo": "payments-service", "risk_tier": "high"},
    ]


def get_service_info(service_name: str) -> Dict[str, Any]:
    services = load_services()
    for s in services:
        if s.get("name") == service_name:
            return s
    return {"name": service_name, "owner": "unassigned", "repo": "unknown", "risk_tier": "medium"}


def _parse_timestamp(ts_val: Any) -> float:
    if isinstance(ts_val, (int, float)):
        # Check if ms or s
        return ts_val / 1000.0 if ts_val > 1e11 else float(ts_val)
    if isinstance(ts_val, str):
        try:
            # Handle ISO formats e.g. 2026-11-24T10:00:00Z
            clean = ts_val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            return dt.timestamp()
        except Exception:
            pass
    return time.time()


class Incident:
    """Normalized Incident model representing external or internal alerts."""
    def __init__(
        self,
        id: str,
        service: str,
        source: str,
        severity: str,
        title: str,
        description: str,
        tags: List[str],
        started_at: float,
        raw: Dict[str, Any],
    ):
        self.id = id
        self.service = service
        self.source = source
        self.severity = severity
        self.title = title
        self.description = description
        self.tags = tags
        self.started_at = started_at
        self.raw = raw

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "service": self.service,
            "source": self.source,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "started_at": self.started_at,
            "raw": self.raw,
        }


# ==============================================================================
# ADAPTERS
# ==============================================================================

def normalize_datadog(payload: dict) -> Incident:
    """Normalize Datadog monitor webhook JSON."""
    raw_id = payload.get("id") or str(uuid.uuid4().hex[:6])
    inc_id = f"inc-dd-{raw_id}"

    # Extract tags
    raw_tags = payload.get("tags") or []
    if isinstance(raw_tags, str):
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    elif isinstance(raw_tags, list):
        tags = [str(t) for t in raw_tags]
    else:
        tags = []

    # Identify service from tags or payload
    service = payload.get("service")
    if not service:
        for t in tags:
            if t.startswith("service:"):
                service = t.split(":", 1)[1]
                break
    service = service or "checkout"

    # Map severity
    alert_type = str(payload.get("alert_type") or payload.get("priority") or "error").lower()
    if alert_type in ["error", "critical"]:
        severity = "critical"
    elif alert_type in ["warn", "warning"]:
        severity = "warning"
    else:
        severity = "info"

    title = payload.get("title") or payload.get("event_type") or "Datadog Monitor Alert"
    description = payload.get("text") or payload.get("body") or ""
    started_at = _parse_timestamp(payload.get("date") or payload.get("last_updated"))

    return Incident(
        id=inc_id,
        service=service,
        source="datadog",
        severity=severity,
        title=title,
        description=description,
        tags=tags,
        started_at=started_at,
        raw=payload,
    )


def normalize_sentry(payload: dict) -> Incident:
    """Normalize Sentry issue alert webhook JSON."""
    data = payload.get("data", {})
    issue = data.get("issue") or payload.get("issue") or {}
    event = data.get("event") or payload.get("event") or {}

    raw_id = issue.get("id") or event.get("event_id") or str(uuid.uuid4().hex[:6])
    inc_id = f"inc-sentry-{raw_id}"

    # Tags
    tags = []
    raw_tags = event.get("tags") or []
    if isinstance(raw_tags, list):
        for t in raw_tags:
            if isinstance(t, list) and len(t) == 2:
                tags.append(f"{t[0]}:{t[1]}")
            elif isinstance(t, dict):
                k = t.get("key")
                v = t.get("value")
                if k and v:
                    tags.append(f"{k}:{v}")
            elif isinstance(t, str):
                tags.append(t)
    elif isinstance(raw_tags, dict):
        tags = [f"{k}:{v}" for k, v in raw_tags.items()]

    service = payload.get("service")
    if not service:
        for t in tags:
            if t.startswith("service:"):
                service = t.split(":", 1)[1]
                break
    service = service or "checkout"

    # Severity
    lvl = str(issue.get("level") or event.get("level") or "error").lower()
    severity = "critical" if lvl in ["fatal", "error"] else ("warning" if lvl == "warning" else "info")

    title = issue.get("title") or event.get("title") or payload.get("message") or "Sentry Issue Alert"
    desc_parts = []
    if issue.get("culprit"):
        desc_parts.append(f"Culprit: {issue['culprit']}")
    meta_val = issue.get("metadata", {}).get("value")
    if meta_val:
        desc_parts.append(meta_val)
    elif event.get("message"):
        desc_parts.append(event["message"])
    description = " - ".join(desc_parts)

    started_at = _parse_timestamp(issue.get("firstSeen") or event.get("timestamp"))

    return Incident(
        id=inc_id,
        service=service,
        source="sentry",
        severity=severity,
        title=title,
        description=description,
        tags=tags,
        started_at=started_at,
        raw=payload,
    )


def normalize_prometheus(payload: dict) -> Incident:
    """Normalize Prometheus Alertmanager webhook JSON (alerts array)."""
    alerts = payload.get("alerts") or []
    first_alert = alerts[0] if alerts else {}
    labels = first_alert.get("labels") or payload.get("commonLabels") or {}
    annotations = first_alert.get("annotations") or payload.get("commonAnnotations") or {}

    raw_id = first_alert.get("fingerprint") or str(uuid.uuid4().hex[:6])
    inc_id = f"inc-prom-{raw_id}"

    service = labels.get("service") or labels.get("job") or payload.get("commonLabels", {}).get("service") or "checkout"
    raw_sev = labels.get("severity") or payload.get("commonLabels", {}).get("severity") or "critical"
    severity = "critical" if raw_sev.lower() in ["critical", "page", "error"] else ("warning" if raw_sev.lower() == "warning" else "info")

    title = annotations.get("summary") or labels.get("alertname") or "Prometheus Alert"
    description = annotations.get("description") or annotations.get("summary") or ""
    tags = [f"{k}:{v}" for k, v in labels.items()]
    started_at = _parse_timestamp(first_alert.get("startsAt"))

    return Incident(
        id=inc_id,
        service=service,
        source="prometheus",
        severity=severity,
        title=title,
        description=description,
        tags=tags,
        started_at=started_at,
        raw=payload,
    )


def normalize_cloudwatch(payload: dict) -> Incident:
    """
    Normalize AWS CloudWatch alarm via SNS.
    Message is a JSON string inside the SNS payload.
    """
    msg_raw = payload.get("Message")
    if isinstance(msg_raw, str):
        try:
            msg_data = json.loads(msg_raw)
        except Exception:
            msg_data = {"AlarmName": payload.get("Subject", "CloudWatch Alarm"), "NewStateReason": msg_raw}
    elif isinstance(msg_raw, dict):
        msg_data = msg_raw
    else:
        msg_data = payload

    raw_id = payload.get("MessageId") or msg_data.get("AlarmArn") or str(uuid.uuid4().hex[:6])
    if "/" in str(raw_id):
        raw_id = str(raw_id).split("/")[-1]
    inc_id = f"inc-cw-{raw_id[:10]}"

    alarm_name = msg_data.get("AlarmName") or payload.get("Subject") or "CloudWatch Alarm"
    state_val = str(msg_data.get("NewStateValue", "ALARM")).upper()
    severity = "critical" if state_val == "ALARM" else ("warning" if state_val == "INSUFFICIENT_DATA" else "info")

    description = msg_data.get("AlarmDescription") or msg_data.get("NewStateReason") or ""

    # Parse dimensions for service
    dimensions = []
    trigger = msg_data.get("Trigger", {})
    if isinstance(trigger, dict) and "Dimensions" in trigger:
        dimensions = trigger["Dimensions"]

    service = "checkout"
    tags = [f"Region:{msg_data.get('Region', 'us-east-1')}"]
    for d in dimensions:
        name = d.get("name") or d.get("Name")
        val = d.get("value") or d.get("Value")
        if name and val:
            tags.append(f"{name}:{val}")
            if name.lower() in ["servicename", "service", "app"]:
                service = val

    started_at = _parse_timestamp(msg_data.get("StateChangeTime") or payload.get("Timestamp"))

    return Incident(
        id=inc_id,
        service=service,
        source="cloudwatch",
        severity=severity,
        title=alarm_name,
        description=description,
        tags=tags,
        started_at=started_at,
        raw=payload,
    )


def normalize_generic(payload: dict, source: str = "generic") -> Incident:
    """Normalize generic webhook payload."""
    inc_id = payload.get("id") or f"inc-gen-{uuid.uuid4().hex[:6]}"
    service = payload.get("service") or "checkout"
    severity = payload.get("severity") or "critical"
    title = payload.get("title") or "Generic Alert"
    description = payload.get("description") or payload.get("message") or ""
    tags = payload.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    started_at = _parse_timestamp(payload.get("started_at") or payload.get("ts"))

    return Incident(
        id=inc_id,
        service=service,
        source=source or "generic",
        severity=severity,
        title=title,
        description=description,
        tags=tags,
        started_at=started_at,
        raw=payload,
    )


def normalize_alert(source: str, payload: dict) -> Incident:
    """Router to adapter based on source string."""
    src = (source or "generic").lower()
    if src == "datadog":
        return normalize_datadog(payload)
    elif src == "sentry":
        return normalize_sentry(payload)
    elif src == "prometheus":
        return normalize_prometheus(payload)
    elif src in ["cloudwatch", "aws", "sns"]:
        return normalize_cloudwatch(payload)
    else:
        return normalize_generic(payload, source=source)


# ==============================================================================
# OTLP LOGS INGESTION
# ==============================================================================

def ingest_otlp_logs(payload: dict) -> int:
    """
    Minimal OTLP/HTTP JSON logs receiver.
    Parses OpenTelemetry JSON log records and appends to audit/ingested_logs.jsonl.
    Returns count of ingested log records.
    """
    records: List[Dict[str, Any]] = []

    resource_logs = payload.get("resourceLogs")
    if isinstance(resource_logs, list):
        for rl in resource_logs:
            res_attrs = {}
            for attr in rl.get("resource", {}).get("attributes", []):
                key = attr.get("key")
                val_obj = attr.get("value", {})
                val = next(iter(val_obj.values())) if isinstance(val_obj, dict) and val_obj else str(val_obj)
                res_attrs[key] = val

            for sl in rl.get("scopeLogs", []):
                for lr in sl.get("logRecords", []):
                    # TimeUnixNano
                    nano = lr.get("timeUnixNano")
                    ts = (int(nano) / 1e9) if nano else time.time()

                    # Level
                    level = lr.get("severityText") or "INFO"

                    # Body
                    body_obj = lr.get("body", {})
                    if isinstance(body_obj, dict):
                        msg = body_obj.get("stringValue") or next(iter(body_obj.values()), "")
                    else:
                        msg = str(body_obj)

                    # Attributes
                    attrs = {}
                    for attr in lr.get("attributes", []):
                        k = attr.get("key")
                        v_obj = attr.get("value", {})
                        v = next(iter(v_obj.values())) if isinstance(v_obj, dict) and v_obj else str(v_obj)
                        # convert numeric if possible
                        try:
                            if "." in str(v):
                                v = float(v)
                            else:
                                v = int(v)
                        except (ValueError, TypeError):
                            pass
                        attrs[k] = v

                    record = {
                        "ts": ts,
                        "level": level,
                        "msg": msg,
                        **res_attrs,
                        **attrs,
                    }
                    records.append(record)
    elif isinstance(payload, list):
        # Direct array of log dictionaries
        records.extend(payload)
    elif isinstance(payload, dict) and "msg" in payload:
        records.append(payload)

    if records:
        os.makedirs(AUDIT_DIR, exist_ok=True)
        with open(INGESTED_LOGS_PATH, "a", encoding="utf-8") as f:
            for rec in records:
                if "ts" not in rec:
                    rec["ts"] = time.time()
                f.write(json.dumps(rec) + "\n")

    return len(records)


def get_ingested_logs(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load ingested OTLP logs."""
    p = path or INGESTED_LOGS_PATH
    if not os.path.exists(p):
        return []
    rows = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return rows
