import os, sys, threading, time, urllib.request, uuid, json, shutil
from typing import Optional
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, Body, Request, Query
from fastapi.middleware.cors import CORSMiddleware
import core, memory, diagnose as dg, codefix, ingest, policy, audit, telegram_bot, n8n

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "dev-secret")
if "WEBHOOK_SECRET" not in os.environ:
    print("[WARNING] WEBHOOK_SECRET not set in environment, defaulting to 'dev-secret'")

INCIDENTS = []
watcher_lock = threading.Lock()
approval_lock = threading.Lock()
INCIDENT_COOLDOWNS = {}  # key -> timestamp
COOLDOWN_SECONDS = 60.0

SNAPSHOTS = {}
snapshot_lock = threading.Lock()

CURRENT_CODEFIX = {
    "proposal": None,
    "approved": False,
    "result": None,
}
codefix_lock = threading.Lock()

BENCHMARK_STATUS = {
    "running": False,
    "completed": 0,
    "total": 0,
    "percent": 0.0,
    "arm": None,
    "fault": None,
    "round": None,
    "error": None,
}
benchmark_lock = threading.Lock()


def verify_webhook_secret(request: Request):
    """Enforce X-Webhook-Secret header authentication."""
    secret = request.headers.get("X-Webhook-Secret")
    if not secret or secret != WEBHOOK_SECRET:
        raise HTTPException(401, "Invalid or missing X-Webhook-Secret header")


def take_snapshot(incident_id: str, fix_type: str = "infrastructure"):
    with snapshot_lock:
        if fix_type == "codefix":
            target_path = getattr(codefix, "TARGET", None) or os.path.join(ROOT_DIR, "demo_app", "checkout_logic.py")
            content = ""
            if os.path.exists(target_path):
                with open(target_path, "r", encoding="utf-8") as f:
                    content = f.read()
            SNAPSHOTS[incident_id] = {
                "type": "codefix",
                "file_path": target_path,
                "content": content,
                "timestamp": time.time(),
            }
        else:
            state = {}
            try:
                state = core.status() if hasattr(core, "status") else {}
            except Exception:
                pass
            SNAPSHOTS[incident_id] = {
                "type": "infrastructure",
                "state": state,
                "timestamp": time.time(),
            }


def execute_rollback(incident_id: str, actor: str = "system:auto_rollback", reason: str = ""):
    with snapshot_lock:
        snapshot = SNAPSHOTS.get(incident_id)

    rolled_back_type = "infrastructure"
    if snapshot and snapshot.get("type") == "codefix":
        file_path = snapshot.get("file_path")
        content = snapshot.get("content")
        if file_path and content:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        if hasattr(codefix, "hot_reload_demo_app"):
            codefix.hot_reload_demo_app()
        rolled_back_type = "codefix"
    else:
        try:
            core.reset()
        except Exception as ex:
            import traceback
            print(f"[watcher exception] {ex}\n{traceback.format_exc()}")

    audit.record_audit_event(
        "rolled_back",
        incident_id=incident_id,
        actor=actor,
        details={"reason": reason, "type": rolled_back_type, "has_snapshot": bool(snapshot)},
    )

    with watcher_lock:
        now = time.time()
        for inc in INCIDENTS:
            if inc.get("id") == incident_id:
                inc["state"] = "ROLLED_BACK"
                inc["status"] = "rolled_back"
                inc["rollback_reason"] = reason
                inc["resolved_at"] = now
                svc = inc.get("service", "checkout")
                sig = inc.get("signature")
                if sig:
                    INCIDENT_COOLDOWNS[f"{svc}:{sig}"] = now
                INCIDENT_COOLDOWNS[svc] = now

    return {"status": "rolled_back", "incident_id": incident_id, "type": rolled_back_type, "reason": reason}


def _ping():
    try:
        urllib.request.urlopen(urllib.request.Request(core.BASE + "/checkout", method="POST"), timeout=10)
    except Exception:
        pass


def _traffic():
    while True:
        threading.Thread(target=_ping, daemon=True).start()
        time.sleep(0.8)


def _watcher():
    consecutive_degraded = 0
    while True:
        time.sleep(2)
        try:
            m = core.status()
            degraded = (m.get("p95_ms", 0) > 500) or (m.get("errors", 0) > 0) or bool(m.get("active_faults"))
            if degraded:
                consecutive_degraded += 1
            else:
                consecutive_degraded = 0

            # If p95>500ms or errors>0 for 3 consecutive polls, open an incident and run diagnose automatically
            if consecutive_degraded >= 3:
                with watcher_lock:
                    service = "checkout"
                    now = time.time()

                    # Deduplication 1: Check if an incident is currently active / open
                    has_open = any(
                        inc.get("state") in ["OPEN", "APPROVED", "APPLIED"]
                        or inc.get("status") in ["detected", "codefix_proposed", "OPEN"]
                        for inc in INCIDENTS
                    )
                    if has_open:
                        continue

                    # Compute fault signature
                    tb = codefix.parse_traceback()
                    if tb:
                        sig = f"codefix:{tb.get('exception')}:{tb.get('file')}"
                    else:
                        active = m.get("active_faults") or []
                        if active:
                            sig = f"fault:{active[0]}"
                        else:
                            sig = f"telemetry:{'p95' if m.get('p95_ms', 0) > 500 else 'err'}"

                    # Deduplication 2: Check 60s cooldown after resolution / rejection
                    cooldown_key = f"{service}:{sig}"
                    last_closed = max(
                        INCIDENT_COOLDOWNS.get(cooldown_key, 0),
                        INCIDENT_COOLDOWNS.get(service, 0),
                    )
                    if (now - last_closed) < COOLDOWN_SECONDS:
                        continue

                    inc_id = f"inc-{uuid.uuid4().hex[:6]}"
                    inc = {
                        "id": inc_id,
                        "service": service,
                        "signature": sig,
                        "source": "auto_detected",
                        "severity": "critical",
                        "title": "Automated SLO Degradation Alert",
                        "description": f"Consecutive degradation observed: p95={m.get('p95_ms', 0)}ms, errors={m.get('errors', 0)}",
                        "tags": ["service:checkout", "source:watcher", f"signature:{sig}"],
                        "started_at": now,
                        "created_at": now,
                        "state": "OPEN",
                        "status": "detected",
                        "notified": False,
                        "detection_snapshot": {
                            "p95_ms": m.get("p95_ms", 0),
                            "errors": m.get("errors", 0),
                        },
                        "diagnosis": None,
                        "evidence": None,
                        "type": "codefix" if tb else "infrastructure",
                        "codefix_proposal": None,
                        "policy_decision": None,
                    }
                    core.INCIDENT["started"] = now
                    core.INCIDENT["wrong_attempts"] = 0

                    # Log detected event
                    audit.record_audit_event(
                        "detected",
                        incident_id=inc_id,
                        actor="system:watcher",
                        details={"p95_ms": m.get("p95_ms"), "errors": m.get("errors")},
                    )

                    # Check if evidence contains traceback -> route to codefix pipeline
                    if tb:
                        inc["type"] = "codefix"
                        inc["status"] = "codefix_proposed"
                        try:
                            proposal = codefix.propose(incident_id=inc_id)
                            proposal["incident_id"] = inc_id
                            inc["codefix_proposal"] = proposal
                            pol = policy.evaluate_policy(
                                action="codefix",
                                confidence=0.90,
                                is_codefix=True,
                                memory_matches=memory.recall({"codefix": True, "code_exception": tb.get("exception"), "code_file": tb.get("file")}),
                            )
                            inc["policy_decision"] = pol
                            audit.record_audit_event("diagnosed", incident_id=inc_id, actor="agent:codefix", details={"summary": proposal.get("summary")})
                            audit.record_audit_event("policy_decision", incident_id=inc_id, actor="policy:engine", details=pol)
                            if not inc.get("notified") and pol.get("decision") == "NEEDS_APPROVAL":
                                inc["notified"] = True
                                telegram_bot.notify_needs_approval(inc)
                            with codefix_lock:
                                CURRENT_CODEFIX["proposal"] = proposal
                                CURRENT_CODEFIX["approved"] = False
                                CURRENT_CODEFIX["result"] = None
                                CURRENT_CODEFIX["incident_id"] = inc_id
                        except Exception as e:
                            print(f"[watcher] auto-codefix failed: {e}")
                    else:
                        # Run diagnose automatically without applying the fix (wait for human approval)
                        try:
                            ev, d = dg.diagnose(use_memory=True)
                            inc["evidence"] = ev
                            inc["diagnosis"] = d
                            pol = policy.evaluate_policy(
                                action=d.get("action", "unknown"),
                                confidence=d.get("confidence", 0.7),
                                evidence=ev,
                                memory_matches=memory.recall(ev),
                            )
                            d["policy_decision"] = pol
                            inc["policy_decision"] = pol
                            audit.record_audit_event("diagnosed", incident_id=inc_id, actor="agent:diagnose", details={"action": d.get("action"), "root_cause": d.get("root_cause")})
                            audit.record_audit_event("policy_decision", incident_id=inc_id, actor="policy:engine", details=pol)
                            if not inc.get("notified") and pol.get("decision") == "NEEDS_APPROVAL":
                                inc["notified"] = True
                                telegram_bot.notify_needs_approval(inc)
                        except Exception as e:
                            print(f"[watcher] auto-diagnose failed: {e}")

                    INCIDENTS.append(inc)
        except Exception as ex:
            import traceback
            print(f"[watcher exception] {ex}\n{traceback.format_exc()}")


def _run_benchmark_task(rounds=3, seed=42):
    from benchmark.run import run_benchmark
    def _cb(p):
        with benchmark_lock:
            BENCHMARK_STATUS.update(p)

    try:
        run_benchmark(rounds=rounds, seed=seed, fast=True, progress_callback=_cb)
        with benchmark_lock:
            BENCHMARK_STATUS["running"] = False
            BENCHMARK_STATUS["percent"] = 100.0
    except Exception as e:
        with benchmark_lock:
            BENCHMARK_STATUS["running"] = False
            BENCHMARK_STATUS["error"] = str(e)


_watcher_thread = None
_traffic_thread = None


def ensure_watcher_started():
    global _watcher_thread
    if _watcher_thread is None or not _watcher_thread.is_alive():
        _watcher_thread = threading.Thread(target=_watcher, daemon=True)
        _watcher_thread.start()


def ensure_traffic_started():
    global _traffic_thread
    if os.environ.get("LIVE_TRAFFIC", "1") == "1":
        if _traffic_thread is None or not _traffic_thread.is_alive():
            _traffic_thread = threading.Thread(target=_traffic, daemon=True)
            _traffic_thread.start()


@asynccontextmanager
async def lifespan(app):
    ensure_traffic_started()
    ensure_watcher_started()
    telegram_bot.start_bot_polling()
    yield
    telegram_bot.stop_bot_polling()


app = FastAPI(title="On-call agent API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Also ensure background workers run even if lifespan is not triggered by host server
ensure_traffic_started()
ensure_watcher_started()
telegram_bot.start_bot_polling()


# ==============================================================================
# ALERT INGESTION & OTLP LOGS
# ==============================================================================

@app.post("/api/ingest/alert")
@app.post("/api/ingest/{source}")
async def ingest_alert(request: Request, source: str = "generic", body: dict = Body(...)):
    """
    Ingest external alerts (Datadog, Sentry, Prometheus, CloudWatch, Generic).
    Requires X-Webhook-Secret header.
    Normalizes to Incident model, logs audit events, runs diagnosis, evaluates policy,
    and auto-applies fix if policy allows.
    """
    verify_webhook_secret(request)
    incident_obj = ingest.normalize_alert(source, body)
    inc_data = incident_obj.to_dict()

    inc_id = inc_data["id"]
    now = time.time()
    inc_data["started_at"] = now
    inc_data["created_at"] = now
    inc_data["state"] = "OPEN"
    inc_data["status"] = "detected"
    inc_data["notified"] = False
    m_snap = {}
    try:
        m_snap = core.status() if hasattr(core, "status") else {}
    except Exception:
        pass
    inc_data["detection_snapshot"] = {
        "p95_ms": m_snap.get("p95_ms", 0),
        "errors": m_snap.get("errors", 0),
    }
    inc_data["type"] = "infrastructure"
    inc_data["diagnosis"] = None
    inc_data["evidence"] = None
    inc_data["codefix_proposal"] = None
    inc_data["policy_decision"] = None

    core.INCIDENT["started"] = now
    core.INCIDENT["wrong_attempts"] = 0

    # 1. Audit event: ingested
    audit.record_audit_event(
        "ingested",
        incident_id=inc_id,
        actor=f"webhook:{source}",
        details={
            "title": incident_obj.title,
            "service": incident_obj.service,
            "severity": incident_obj.severity,
            "source": incident_obj.source,
            "tags": incident_obj.tags,
        },
    )

    # 2. Audit event: detected
    audit.record_audit_event(
        "detected",
        incident_id=inc_id,
        actor=f"webhook:{source}",
        details={"service": incident_obj.service, "title": incident_obj.title},
    )

    # Check for code traceback
    tb = codefix.parse_traceback()
    if tb:
        inc_data["type"] = "codefix"
        inc_data["status"] = "codefix_proposed"
        try:
            proposal = codefix.propose(incident_id=inc_id)
            inc_data["codefix_proposal"] = proposal
            pol = policy.evaluate_policy(
                action="codefix",
                confidence=0.90,
                is_codefix=True,
                memory_matches=memory.recall({"codefix": True, "code_exception": tb.get("exception"), "code_file": tb.get("file")}),
            )
            inc_data["policy_decision"] = pol
            audit.record_audit_event("diagnosed", incident_id=inc_id, actor="agent:codefix", details={"summary": proposal.get("summary")})
            audit.record_audit_event("policy_decision", incident_id=inc_id, actor="policy:engine", details=pol)
            if not inc_data.get("notified") and pol.get("decision") == "NEEDS_APPROVAL":
                inc_data["notified"] = True
                telegram_bot.notify_needs_approval(inc_data)
            with codefix_lock:
                CURRENT_CODEFIX["proposal"] = proposal
                CURRENT_CODEFIX["approved"] = False
                CURRENT_CODEFIX["result"] = None
                CURRENT_CODEFIX["incident_id"] = inc_id
        except Exception as e:
            print(f"[ingest] codefix propose failed: {e}")
    else:
        try:
            ev, d = dg.diagnose(use_memory=True, external_source=source)
            inc_data["evidence"] = ev
            inc_data["diagnosis"] = d

            mem_matches = memory.recall(ev)
            pol = policy.evaluate_policy(
                action=d.get("action", "unknown"),
                confidence=d.get("confidence", 0.7),
                evidence=ev,
                memory_matches=mem_matches,
                source=source,
                root_cause=d.get("root_cause"),
            )
            d["policy_decision"] = pol
            inc_data["policy_decision"] = pol

            audit.record_audit_event(
                "diagnosed",
                incident_id=inc_id,
                actor="agent:diagnose",
                details={"action": d.get("action"), "root_cause": d.get("root_cause"), "confidence": d.get("confidence")},
            )
            audit.record_audit_event("policy_decision", incident_id=inc_id, actor="policy:engine", details=pol)

            if not inc_data.get("notified") and pol.get("decision") == "NEEDS_APPROVAL":
                inc_data["notified"] = True
                telegram_bot.notify_needs_approval(inc_data)

            # Auto-apply if policy allows
            if pol.get("decision") == "AUTO_APPLY":
                policy.record_auto_applied_fix()
                inc_data["state"] = "APPROVED"
                audit.record_audit_event("approved", incident_id=inc_id, actor="policy:auto_apply", details={"action": d.get("action")})
                take_snapshot(inc_id, "infrastructure")

                inc_data["state"] = "APPLIED"
                audit.record_audit_event("applied", incident_id=inc_id, actor="policy:auto_apply", details={"action": d.get("action")})
                fix_res = core.apply_fix(d["action"], d, ev, use_memory=True)

                recovered = fix_res.get("recovered", False)
                before_m = fix_res.get("before", {})
                after_m = fix_res.get("after", {})
                audit.record_audit_event(
                    "verified",
                    incident_id=inc_id,
                    actor="system:verification",
                    details={"recovered": recovered, "before": before_m, "after": after_m},
                )

                p95_before = before_m.get("p95_ms") or 0
                p95_after = after_m.get("p95_ms") or 0
                errors_after = after_m.get("errors", 0)

                # Auto-rollback only if unhealthy by absolute health definition
                slo_val = 200 if getattr(core, "FAST_MODE", False) else policy.get_slo_p95_ms()
                healthy = policy.is_service_healthy(after_m, before_m, slo_p95_ms=slo_val)
                if not healthy:
                    execute_rollback(
                        inc_id,
                        actor="system:auto_rollback",
                        reason=f"Verification failed: {errors_after} errors, p95 {p95_after}ms",
                    )
                    inc_data["state"] = "ROLLED_BACK"
                    inc_data["status"] = "rolled_back"
                elif recovered:
                    inc_data["state"] = "VERIFIED"
                    inc_data["status"] = "VERIFIED"
                    inc_data["resolved_at"] = time.time()
                    inc_data["fix_result"] = fix_res
        except Exception as e:
            print(f"[ingest] diagnose failed: {e}")

    with watcher_lock:
        INCIDENTS.append(inc_data)

    return {"status": "ingested", "incident": inc_data, "policy": inc_data.get("policy_decision")}


@app.post("/v1/logs")
def receive_otlp_logs(payload: dict = Body(...)):
    """OTLP/HTTP JSON logs receiver."""
    count = ingest.ingest_otlp_logs(payload)
    return {"status": "accepted", "records_ingested": count}


# ==============================================================================
# AUDIT & POLICY ENDPOINTS
# ==============================================================================

@app.get("/api/audit")
def get_audit(incident_id: Optional[str] = Query(None)):
    """Return append-only audit trail, optionally filtered by incident_id."""
    return audit.get_audit_log(incident_id)


@app.get("/api/audit/verify")
def verify_audit():
    """Verify cryptographic hash-chain integrity of audit log."""
    return audit.verify_audit_chain()


@app.get("/api/policies")
def list_policies():
    """List loaded policies and risk tiers."""
    return policy.load_policies()


@app.get("/api/services")
def list_services():
    """List registered services from services.yaml."""
    return ingest.load_services()


@app.post("/api/rollback")
def rollback_fix(body: dict = Body(default={})):
    """Manual rollback endpoint."""
    inc_id = body.get("incident_id")
    if not inc_id:
        with watcher_lock:
            if INCIDENTS:
                inc_id = INCIDENTS[-1].get("id")
    if not inc_id:
        inc_id = "inc-active"

    actor = body.get("actor", "human:operator")
    reason = body.get("reason", "Manual rollback requested via dashboard")
    return execute_rollback(inc_id, actor=actor, reason=reason)


# ==============================================================================
# CORE CONTROL ENDPOINTS
# ==============================================================================

@app.get("/api/state")
def state():
    base = {
        "mode": dg.mode(),
        "memory_count": len(memory.load()),
        "has_codefix": bool(CURRENT_CODEFIX.get("proposal")),
    }
    try:
        return {"online": True, **base, **core.status()}
    except Exception:
        return {"online": False, **base}


@app.post("/api/break")
async def break_it(request: Request, fault: str = "pool_exhaustion"):
    try:
        body = await request.json()
        if isinstance(body, dict) and "fault" in body:
            fault = body["fault"]
    except Exception:
        pass

    try:
        return core.break_service(fault)
    except Exception as e:
        raise HTTPException(502, f"Demo service unreachable: {e}")


@app.post("/api/reset")
def reset():
    try:
        core.reset()
        with watcher_lock:
            for inc in INCIDENTS:
                if inc.get("status") in ["detected", "codefix_proposed"]:
                    inc["status"] = "dismissed"
        with codefix_lock:
            CURRENT_CODEFIX["proposal"] = None
            CURRENT_CODEFIX["approved"] = False
            CURRENT_CODEFIX["result"] = None
    except Exception as e:
        raise HTTPException(502, f"Demo service unreachable: {e}")
    return {"ok": True}


@app.post("/api/diagnose")
def diagnose(use_memory: bool = True, source: Optional[str] = None):
    ext_source = source
    if not ext_source:
        with watcher_lock:
            open_incs = [i for i in INCIDENTS if i.get("status") in ["detected", "codefix_proposed"]]
            if open_incs and open_incs[-1].get("source") not in ["auto_detected", "watcher", None]:
                ext_source = open_incs[-1].get("source")

    ev, d = dg.diagnose(use_memory, external_source=ext_source)
    if not ev.get("incident_detected") and not ext_source:
        raise HTTPException(400, "No incident evidence yet. Click 'Break service' first.")

    # Evaluate policy and attach to diagnosis
    mem_matches = memory.recall(ev) if use_memory else []
    pol = policy.evaluate_policy(
        action=d.get("action", "unknown"),
        confidence=d.get("confidence", 0.7),
        evidence=ev,
        memory_matches=mem_matches,
        source=ext_source,
        root_cause=d.get("root_cause"),
    )
    d["policy_decision"] = pol

    # Find open incident to associate
    inc_id = None
    with watcher_lock:
        open_incs = [i for i in INCIDENTS if i.get("status") in ["detected", "codefix_proposed"]]
        if open_incs:
            inc_id = open_incs[-1].get("id")
            open_incs[-1]["diagnosis"] = d
            open_incs[-1]["evidence"] = ev
            open_incs[-1]["policy_decision"] = pol

    if inc_id:
        audit.record_audit_event("diagnosed", incident_id=inc_id, actor="agent:diagnose", details={"action": d.get("action"), "root_cause": d.get("root_cause")})
        audit.record_audit_event("policy_decision", incident_id=inc_id, actor="policy:engine", details=pol)
        if pol.get("decision") == "NEEDS_APPROVAL":
            with watcher_lock:
                for i in INCIDENTS:
                    if i.get("id") == inc_id:
                        if not i.get("notified"):
                            i["notified"] = True
                            telegram_bot.notify_needs_approval(i)
                        break

    return {"evidence": ev, "diagnosis": d, "use_memory": use_memory, "policy": pol}


def get_incident_by_id(inc_id: str) -> Optional[dict]:
    with watcher_lock:
        for inc in INCIDENTS:
            if inc.get("id") == inc_id:
                return inc
    return None


def dismiss_incident(inc_id: str, actor: str = "human:operator", reason: str = "") -> dict:
    with approval_lock:
        target_inc = None
        with watcher_lock:
            for inc in INCIDENTS:
                if inc.get("id") == inc_id:
                    target_inc = inc
                    break

        if target_inc:
            current_state = target_inc.get("state", "OPEN")
            if current_state != "OPEN":
                return {
                    "ok": False,
                    "status": "already_handled",
                    "message": f"Already handled: {current_state}",
                    "state": current_state,
                    "incident_id": inc_id,
                }
            target_inc["state"] = "REJECTED"
            target_inc["status"] = "rejected"
            target_inc["dismissed_by"] = actor
            target_inc["dismissed_reason"] = reason
            now = time.time()
            target_inc["resolved_at"] = now
            svc = target_inc.get("service", "checkout")
            sig = target_inc.get("signature")
            if sig:
                INCIDENT_COOLDOWNS[f"{svc}:{sig}"] = now
            INCIDENT_COOLDOWNS[svc] = now

        audit.record_audit_event("rejected", incident_id=inc_id, actor=actor, details={"reason": reason or "Dismissed"})
        return {"status": "rejected", "incident_id": inc_id}


def apply_infra_fix(
    action: str,
    diag: dict,
    ev: dict,
    use_mem: bool = True,
    actor: str = "human:operator",
    inc_id: Optional[str] = None,
) -> dict:
    with approval_lock:
        target_inc = None
        with watcher_lock:
            if inc_id:
                for i in INCIDENTS:
                    if i.get("id") == inc_id:
                        target_inc = i
                        break
            if not target_inc:
                open_incs = [
                    i for i in INCIDENTS
                    if i.get("state") in ["OPEN", "APPROVED", "APPLIED"]
                    or i.get("status") in ["detected", "codefix_proposed", "OPEN"]
                ]
                if open_incs:
                    target_inc = open_incs[-1]
                    inc_id = target_inc.get("id")

        if not inc_id:
            inc_id = f"inc-{uuid.uuid4().hex[:6]}"

        if target_inc:
            current_state = target_inc.get("state", "OPEN")
            if current_state != "OPEN":
                return {
                    "ok": False,
                    "status": "already_handled",
                    "message": f"Already handled: {current_state}",
                    "state": current_state,
                    "recovered": current_state == "VERIFIED",
                    "incident_id": inc_id,
                }
            target_inc["state"] = "APPROVED"
            target_inc["approved_by"] = actor
            target_inc["approved_at"] = time.time()

        audit.record_audit_event("approved", incident_id=inc_id, actor=actor, details={"action": action})
        take_snapshot(inc_id, "infrastructure")

        if target_inc:
            target_inc["state"] = "APPLIED"

        audit.record_audit_event("applied", incident_id=inc_id, actor=actor, details={"action": action})
        res = core.apply_fix(action, diag, ev, use_mem)

        recovered = res.get("recovered", False)
        before_m = res.get("before", {})
        after_m = res.get("after", {})

        p95_before = (
            (target_inc.get("detection_snapshot") or {}).get("p95_ms")
            if target_inc
            else None
        ) or before_m.get("p95_ms") or 0
        p95_after = after_m.get("p95_ms") or 0
        errors_after = after_m.get("errors", 0)

        res["before"] = {"p95_ms": p95_before, "errors": before_m.get("errors", 0)}
        res["after"] = {"p95_ms": p95_after, "errors": errors_after}

        slo_val = 200 if getattr(core, "FAST_MODE", False) else policy.get_slo_p95_ms()
        healthy = policy.is_service_healthy(after_m, before_m, slo_p95_ms=slo_val)
        if not healthy:
            execute_rollback(
                inc_id,
                actor="system:auto_rollback",
                reason=f"Verification failed: {errors_after} errors, p95 {p95_after}ms",
            )
            audit.record_audit_event(
                "verified",
                incident_id=inc_id,
                actor="system:verification",
                details={"recovered": False, "before": res["before"], "after": res["after"]},
            )
            if target_inc:
                target_inc["state"] = "ROLLED_BACK"
                target_inc["status"] = "rolled_back"
            res["recovered"] = False
            res["auto_rolled_back"] = True
        elif recovered:
            audit.record_audit_event(
                "verified",
                incident_id=inc_id,
                actor="system:verification",
                details={"recovered": True, "before": res["before"], "after": res["after"]},
            )
            now = time.time()
            if target_inc:
                target_inc["state"] = "VERIFIED"
                target_inc["status"] = "VERIFIED"
                target_inc["resolved_at"] = now
                target_inc["fix_result"] = res
                svc = target_inc.get("service", "checkout")
                sig = target_inc.get("signature")
                if sig:
                    INCIDENT_COOLDOWNS[f"{svc}:{sig}"] = now
                INCIDENT_COOLDOWNS[svc] = now
            else:
                with watcher_lock:
                    for inc in INCIDENTS:
                        if inc.get("id") == inc_id or inc.get("status") == "detected":
                            inc["state"] = "VERIFIED"
                            inc["status"] = "VERIFIED"
                            inc["resolved_at"] = now
                            inc["fix_result"] = res

        return res


def apply_code_fix(proposal: dict, actor: str = "human:operator") -> dict:
    with approval_lock:
        inc_id = proposal.get("incident_id")
        target_inc = None
        with watcher_lock:
            if inc_id:
                for i in INCIDENTS:
                    if i.get("id") == inc_id:
                        target_inc = i
                        break
            if not target_inc:
                open_incs = [
                    i for i in INCIDENTS
                    if i.get("state") in ["OPEN", "APPROVED", "APPLIED"]
                    or i.get("status") in ["detected", "codefix_proposed", "OPEN"]
                ]
                if open_incs:
                    target_inc = open_incs[-1]
                    inc_id = target_inc.get("id")

        if not inc_id:
            inc_id = "inc-active"

        if target_inc:
            current_state = target_inc.get("state", "OPEN")
            if current_state != "OPEN":
                return {
                    "ok": False,
                    "status": "already_handled",
                    "message": f"Already handled: {current_state}",
                    "state": current_state,
                    "recovered": current_state == "VERIFIED",
                    "incident_id": inc_id,
                }
            target_inc["state"] = "APPROVED"
            target_inc["approved_by"] = actor
            target_inc["approved_at"] = time.time()

        audit.record_audit_event("approved", incident_id=inc_id, actor=actor, details={"type": "codefix"})
        take_snapshot(inc_id, "codefix")

        if target_inc:
            target_inc["state"] = "APPLIED"

        try:
            audit.record_audit_event("applied", incident_id=inc_id, actor=actor, details={"type": "codefix"})
            res = codefix.approve(proposal)

            recovered = bool(res.get("recovered"))
            pr_url = res.get("pr_url") or (res.get("git", {}).get("pr_url"))

            before_m = res.get("before", {})
            after_m = res.get("after", {})
            p95_before = (
                (target_inc.get("detection_snapshot") or {}).get("p95_ms")
                if target_inc
                else None
            ) or before_m.get("p95_ms") or 0
            p95_after = after_m.get("p95_ms") or res.get("metrics", {}).get("p95_ms", 0)
            errors_after = after_m.get("errors", 0) if "errors" in after_m else res.get("metrics", {}).get("errors", 0)

            res["before"] = {"p95_ms": p95_before, "errors": before_m.get("errors", 0)}
            res["after"] = {"p95_ms": p95_after, "errors": errors_after}

            audit.record_audit_event("verified", incident_id=inc_id, actor="system:verification", details=res)

            if not recovered:
                execute_rollback(inc_id, actor="system:auto_rollback", reason="Codefix verification failed")
                if target_inc:
                    target_inc["state"] = "ROLLED_BACK"
                    target_inc["status"] = "rolled_back"
                res["auto_rolled_back"] = True
            else:
                now = time.time()
                if target_inc:
                    target_inc["state"] = "VERIFIED"
                    target_inc["status"] = "VERIFIED"
                    target_inc["resolved_at"] = now
                    target_inc["codefix_result"] = res
                    if pr_url:
                        target_inc["pr_url"] = pr_url
                    svc = target_inc.get("service", "checkout")
                    sig = target_inc.get("signature")
                    if sig:
                        INCIDENT_COOLDOWNS[f"{svc}:{sig}"] = now
                    INCIDENT_COOLDOWNS[svc] = now

            with codefix_lock:
                CURRENT_CODEFIX["approved"] = True
                CURRENT_CODEFIX["result"] = res
                if pr_url:
                    CURRENT_CODEFIX["pr_url"] = pr_url
            with watcher_lock:
                for inc in INCIDENTS:
                    if inc.get("id") == inc_id or inc.get("status") in ["detected", "codefix_proposed"]:
                        inc["state"] = "VERIFIED" if recovered else "ROLLED_BACK"
                        inc["status"] = "VERIFIED" if recovered else "failed"
                        inc["resolved_at"] = time.time()
                        inc["codefix_result"] = res
                        if pr_url:
                            inc["pr_url"] = pr_url
            return res
        except Exception as e:
            execute_rollback(inc_id, actor="system:auto_rollback", reason=f"Exception during codefix: {e}")
            if target_inc:
                target_inc["state"] = "ROLLED_BACK"
                target_inc["status"] = "failed"
            raise e


def execute_approve_by_id(inc_id: str, actor: str = "human:operator") -> dict:
    inc = get_incident_by_id(inc_id)
    if inc and (inc.get("type") == "codefix" or inc.get("codefix_proposal")):
        prop = inc.get("codefix_proposal") or CURRENT_CODEFIX.get("proposal")
        if prop:
            return apply_code_fix(prop, actor=actor)
    elif CURRENT_CODEFIX.get("proposal") and CURRENT_CODEFIX["proposal"].get("incident_id") == inc_id:
        return apply_code_fix(CURRENT_CODEFIX["proposal"], actor=actor)

    diag = (inc.get("diagnosis") if inc else {}) or {}
    ev = (inc.get("evidence") if inc else {}) or {}
    action = diag.get("action", "scale_pool")
    return apply_infra_fix(action, diag, ev, use_mem=True, actor=actor, inc_id=inc_id)


@app.post("/api/fix")
def fix(body: dict = Body(...)):
    action = body["action"]
    diag = body.get("diagnosis", {})
    ev = body.get("evidence", {})
    use_mem = body.get("use_memory", True)
    actor = body.get("actor", "human:operator")
    inc_id = body.get("incident_id")

    try:
        return apply_infra_fix(action, diag, ev, use_mem=use_mem, actor=actor, inc_id=inc_id)
    except Exception as e:
        raise HTTPException(502, f"Fix failed: {e}")


@app.get("/api/incidents")
def list_incidents():
    return INCIDENTS


@app.get("/api/memory")
def get_memory():
    return memory.load()


@app.delete("/api/memory")
def clear_memory():
    memory.clear()
    return {"ok": True}


@app.post("/api/admin/reset-demo-data")
def reset_demo_data():
    """
    Clears audit, memory, incidents, and ingested logs (not benchmark results).
    """
    data_dir = os.environ.get("DATA_DIR", os.path.join(ROOT_DIR, "data"))

    # 1. Clear memory
    memory.clear()

    # 2. Clear audit log
    audit_log = os.path.join(data_dir, "audit.log")
    if os.path.exists(audit_log):
        try:
            os.remove(audit_log)
        except Exception:
            pass

    # 3. Clear ingested logs
    ingested_logs = os.path.join(data_dir, "ingested_logs.jsonl")
    if os.path.exists(ingested_logs):
        try:
            os.remove(ingested_logs)
        except Exception:
            pass

    # 4. Clear incident reports
    reports_dir = os.path.join(data_dir, "incident_reports")
    if os.path.exists(reports_dir):
        try:
            shutil.rmtree(reports_dir)
        except Exception:
            pass

    # 5. Clear runtime incidents, snapshots, codefix, cooldowns
    with watcher_lock:
        INCIDENTS.clear()
        INCIDENT_COOLDOWNS.clear()
    with snapshot_lock:
        SNAPSHOTS.clear()
    with codefix_lock:
        CURRENT_CODEFIX["proposal"] = None
        CURRENT_CODEFIX["approved"] = False
        CURRENT_CODEFIX["result"] = None
        CURRENT_CODEFIX["incident_id"] = None

    return {"status": "ok", "message": "Demo data reset successfully"}


# Benchmark endpoints
@app.get("/api/benchmark")
def get_benchmark():
    res_path = os.path.join(ROOT_DIR, "benchmark", "results.json")
    if os.path.exists(res_path):
        try:
            with open(res_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            raise HTTPException(500, f"Error reading benchmark results: {e}")
    return {}


@app.post("/api/benchmark/run")
def start_benchmark(body: dict = Body(default={})):
    rounds = body.get("rounds", 3)
    seed = body.get("seed", 42)
    with benchmark_lock:
        if BENCHMARK_STATUS["running"]:
            return {"status": "already_running", "benchmark": BENCHMARK_STATUS}
        BENCHMARK_STATUS.update({
            "running": True,
            "completed": 0,
            "total": rounds * 6 * 2,
            "percent": 0.0,
            "arm": None,
            "fault": None,
            "round": None,
            "error": None,
        })
    t = threading.Thread(target=_run_benchmark_task, args=(rounds, seed), daemon=True)
    t.start()
    return {"status": "started", "rounds": rounds, "seed": seed}


@app.get("/api/benchmark/status")
def get_benchmark_status():
    with benchmark_lock:
        return dict(BENCHMARK_STATUS)


# Code-fix endpoints
@app.post("/api/codefix/propose")
def propose_codefix(body: dict = Body(default={})):
    incident_id = body.get("incident_id")
    target_inc = None
    with watcher_lock:
        if incident_id:
            for inc in INCIDENTS:
                if inc.get("id") == incident_id:
                    target_inc = inc
                    break
        if not target_inc:
            open_incs = [
                i for i in INCIDENTS
                if i.get("state") in ["OPEN", "APPROVED", "APPLIED"]
                or i.get("status") in ["detected", "codefix_proposed", "OPEN"]
            ]
            if open_incs:
                target_inc = open_incs[-1]
                incident_id = target_inc.get("id")

    proposal = codefix.propose(incident_id=incident_id)
    if "error" in proposal:
        raise HTTPException(400, proposal["error"])
    if incident_id:
        proposal["incident_id"] = incident_id

    with codefix_lock:
        CURRENT_CODEFIX["proposal"] = proposal
        CURRENT_CODEFIX["approved"] = False
        CURRENT_CODEFIX["result"] = None
        CURRENT_CODEFIX["incident_id"] = proposal.get("incident_id")

    # Evaluate policy for codefix
    pol = policy.evaluate_policy(
        action="codefix",
        confidence=0.90,
        is_codefix=True,
    )
    proposal["policy_decision"] = pol

    if target_inc:
        target_inc["codefix_proposal"] = proposal
        target_inc["policy_decision"] = pol
        if not target_inc.get("notified") and pol.get("decision") == "NEEDS_APPROVAL":
            target_inc["notified"] = True
            telegram_bot.notify_needs_approval(target_inc)
    elif pol.get("decision") == "NEEDS_APPROVAL":
        telegram_bot.notify_needs_approval({
            "id": proposal.get("incident_id") or "inc-codefix",
            "service": "checkout",
            "source": "auto_detected",
            "title": proposal.get("summary") or "Automated Code Fix Proposed",
            "type": "codefix",
            "codefix_proposal": proposal,
            "policy_decision": pol,
        })

    return proposal


@app.post("/api/codefix/approve")
def approve_codefix(body: dict = Body(default={})):
    proposal = body.get("proposal") or CURRENT_CODEFIX.get("proposal")
    if not proposal:
        raise HTTPException(400, "No active codefix proposal to approve.")

    actor = body.get("actor", "human:operator")
    try:
        return apply_code_fix(proposal, actor=actor)
    except Exception as e:
        raise HTTPException(500, f"Code fix application failed: {e}")


@app.get("/api/integrations")
def get_integrations():
    """Return status chips for GitHub, Telegram, and n8n without exposing secrets."""
    gh_token = os.environ.get("GITHUB_TOKEN")
    tg_configured = telegram_bot.is_configured()
    n8n_configured = n8n.is_configured()

    return {
        "github": {
            "configured": bool(gh_token),
            "status": "Configured (token active)" if gh_token else "Not configured",
            "feature": "Pull Request creation & branch pushing",
        },
        "telegram": {
            "configured": tg_configured,
            "status": "Configured (polling active)" if tg_configured else "Not configured",
            "feature": "Interactive 2-way approval via Telegram bot",
        },
        "n8n": {
            "configured": n8n_configured,
            "status": "Configured (HMAC signed webhook)" if n8n_configured else "Not configured",
            "feature": "Outbound webhook dispatch for workflow automation",
        },
    }


@app.get("/api/codefix/status")
def get_codefix_status():
    with codefix_lock:
        return dict(CURRENT_CODEFIX)


@app.post("/api/bug/inject")
def inject_bug_endpoint(name: str):
    try:
        return core.break_bug(name)
    except Exception as e:
        raise HTTPException(502, f"Failed to inject bug: {e}")


@app.post("/api/bug/restore")
def restore_bug_endpoint():
    try:
        core.restore_bug()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(502, f"Failed to restore bug: {e}")
