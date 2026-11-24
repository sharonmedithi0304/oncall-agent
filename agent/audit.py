import os
import json
import time
import uuid
import hashlib
import threading
from typing import Optional, List, Dict, Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_dir() -> str:
    d = os.environ.get("DATA_DIR", os.path.join(ROOT_DIR, "data"))
    os.makedirs(d, exist_ok=True)
    return d


def get_default_audit_log() -> str:
    return os.path.join(get_data_dir(), "audit.log")


GENESIS_HASH = "0" * 64
_audit_lock = threading.Lock()


def _compute_hash(entry_data: dict, prev_hash: str) -> str:
    """Compute deterministic SHA-256 hash of entry fields + prev_hash."""
    # Exclude 'hash' key if present
    data_to_hash = {k: v for k, v in entry_data.items() if k != "hash"}
    canonical_json = json.dumps(data_to_hash, sort_keys=True, separators=(",", ":"))
    combined = f"{canonical_json}|{prev_hash}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _get_last_hash(log_path: str) -> str:
    """Read the last entry's hash from the audit log, or return GENESIS_HASH if empty/missing."""
    if not os.path.exists(log_path):
        return GENESIS_HASH
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        if not lines:
            return GENESIS_HASH
        last_entry = json.loads(lines[-1])
        return last_entry.get("hash", GENESIS_HASH)
    except Exception:
        return GENESIS_HASH


def record_audit_event(
    event: str,
    incident_id: Optional[str] = None,
    actor: str = "system",
    details: Optional[Dict[str, Any]] = None,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Append-only, hash-chained audit logger.
    Events: ingested, detected, diagnosed, policy_decision, approved, rejected, applied, verified, rolled_back.
    """
    path = log_path or get_default_audit_log()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    with _audit_lock:
        prev_hash = _get_last_hash(path)
        entry_id = f"aud-{uuid.uuid4().hex[:12]}"
        entry = {
            "id": entry_id,
            "timestamp": time.time(),
            "incident_id": incident_id,
            "event": event,
            "actor": actor,
            "details": details or {},
            "prev_hash": prev_hash,
        }
        entry["hash"] = _compute_hash(entry, prev_hash)

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    return entry


def get_audit_log(
    incident_id: Optional[str] = None,
    log_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve audit events, optionally filtered by incident_id."""
    path = log_path or get_default_audit_log()
    if not os.path.exists(path):
        return []

    entries = []
    with _audit_lock:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if incident_id is None or e.get("incident_id") == incident_id:
                        entries.append(e)
                except Exception:
                    pass
    return entries


def verify_audit_chain(log_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Verifies the cryptographic integrity of the hash chain in the audit log.
    Returns:
        {
            "verified": bool,
            "total_entries": int,
            "error": str | None,
            "tampered_index": int | None,
        }
    """
    path = log_path or get_default_audit_log()
    if not os.path.exists(path):
        return {"verified": True, "total_entries": 0, "error": None, "tampered_index": None}

    with _audit_lock:
        entries = []
        with open(path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append((idx, json.loads(line)))
                except Exception as ex:
                    return {
                        "verified": False,
                        "total_entries": len(entries),
                        "tampered_index": idx,
                        "error": f"Invalid JSON on line {idx + 1}: {ex}",
                    }

    expected_prev = GENESIS_HASH
    for i, (line_num, entry) in enumerate(entries):
        prev_hash = entry.get("prev_hash")
        stored_hash = entry.get("hash")

        # 1. Check prev_hash links to previous block's hash
        if prev_hash != expected_prev:
            return {
                "verified": False,
                "total_entries": len(entries),
                "tampered_index": i,
                "error": (
                    f"Chain broken at entry index {i} (line {line_num + 1}): "
                    f"prev_hash '{prev_hash}' does not match expected '{expected_prev}'"
                ),
            }

        # 2. Check current entry hash matches freshly computed hash
        computed_hash = _compute_hash(entry, prev_hash)
        if stored_hash != computed_hash:
            return {
                "verified": False,
                "total_entries": len(entries),
                "tampered_index": i,
                "error": (
                    f"Tamper detected at entry index {i} (line {line_num + 1}): "
                    f"stored hash '{stored_hash}' does not match recomputed '{computed_hash}'"
                ),
            }

        expected_prev = stored_hash

    return {
        "verified": True,
        "total_entries": len(entries),
        "error": None,
        "tampered_index": None,
    }
