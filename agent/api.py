import os, sys, threading, time, urllib.request, uuid, json
from typing import Optional
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, Body, Request, Query
from fastapi.middleware.cors import CORSMiddleware
import core, memory, diagnose as dg, codefix, ingest, policy, audit

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "dev-secret")
if "WEBHOOK_SECRET" not in os.environ:
    print("[WARNING] WEBHOOK_SECRET not set in environment, defaulting to 'dev-secret'")

INCIDENTS = []
watcher_lock = threading.Lock()

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
            SNAPSHOTS[incident_id] = {
                "type": "infrastructure",
                "state": core.status() if hasattr(core, "status") else {},
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
        for inc in INCIDENTS:
            if inc.get("id") == incident_id:
                inc["status"] = "rolled_back"
                inc["rollback_reason"] = reason

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
                    has_open = any(inc.get("status") in ["detected", "codefix_proposed"] for inc in INCIDENTS)
                    if not has_open:
                        inc_id = f"inc-{uuid.uuid4().hex[:6]}"
                        now = time.time()
                        inc = {
                            "id": inc_id,
                            "service": "checkout",
                            "source": "auto_detected",
                            "severity": "critical",
                            "title": "Automated SLO Degradation Alert",
                            "description": f"Consecutive degradation observed: p95={m.get('p95_ms', 0)}ms, errors={m.get('errors', 0)}",
                            "tags": ["service:checkout", "source:watcher"],
                            "started_at": now,
                            "status": "detected",
                            "diagnosis": None,
                            "evidence": None,
                            "type": "infrastructure",
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
                        tb = codefix.parse_traceback()
                        if tb:
                            inc["type"] = "codefix"
                            inc["status"] = "codefix_proposed"
                            try:
                                proposal = codefix.propose(incident_id=inc_id)
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
                                with codefix_lock:
                                    CURRENT_CODEFIX["proposal"] = proposal
                                    CURRENT_CODEFIX["approved"] = False
                                    CURRENT_CODEFIX["result"] = None
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
    yield


app = FastAPI(title="On-call agent API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Also ensure background workers run even if lifespan is not triggered by host server
ensure_traffic_started()
ensure_watcher_started()


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
    inc_data["status"] = "detected"
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
            with codefix_lock:
                CURRENT_CODEFIX["proposal"] = proposal
                CURRENT_CODEFIX["approved"] = False
                CURRENT_CODEFIX["result"] = None
        except Exception as e:
            print(f"[ingest] codefix propose failed: {e}")
    else:
        try:
            ev, d = dg.diagnose(use_memory=True)
            inc_data["evidence"] = ev
            inc_data["diagnosis"] = d

            mem_matches = memory.recall(ev)
            pol = policy.evaluate_policy(
                action=d.get("action", "unknown"),
                confidence=d.get("confidence", 0.7),
                evidence=ev,
                memory_matches=mem_matches,
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

            # Auto-apply if policy allows
            if pol.get("decision") == "AUTO_APPLY":
                policy.record_auto_applied_fix()
                audit.record_audit_event("approved", incident_id=inc_id, actor="policy:auto_apply", details={"action": d.get("action")})
                take_snapshot(inc_id, "infrastructure")

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

                # Auto-rollback if verification fails (errors>0 or p95 worse than threshold/before)
                if not recovered or errors_after > 0 or (p95_before > 0 and p95_after > p95_before and p95_after >= (200 if getattr(core, "FAST_MODE", False) else 500)):
                    execute_rollback(
                        inc_id,
                        actor="system:auto_rollback",
                        reason=f"Verification failed: {errors_after} errors, p95 {p95_after}ms",
                    )
                elif recovered:
                    inc_data["status"] = "resolved"
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
def diagnose(use_memory: bool = True):
    ev, d = dg.diagnose(use_memory)
    if not ev["incident_detected"]:
        raise HTTPException(400, "No incident evidence yet. Click 'Break service' first.")

    # Evaluate policy and attach to diagnosis
    mem_matches = memory.recall(ev) if use_memory else []
    pol = policy.evaluate_policy(
        action=d.get("action", "unknown"),
        confidence=d.get("confidence", 0.7),
        evidence=ev,
        memory_matches=mem_matches,
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

    return {"evidence": ev, "diagnosis": d, "use_memory": use_memory, "policy": pol}


@app.post("/api/fix")
def fix(body: dict = Body(...)):
    action = body["action"]
    diag = body.get("diagnosis", {})
    ev = body.get("evidence", {})
    use_mem = body.get("use_memory", True)

    inc_id = None
    with watcher_lock:
        open_incs = [i for i in INCIDENTS if i.get("status") == "detected"]
        if open_incs:
            inc_id = open_incs[-1].get("id")

    if not inc_id:
        inc_id = f"inc-{uuid.uuid4().hex[:6]}"

    # Audit approved & take snapshot
    audit.record_audit_event("approved", incident_id=inc_id, actor="human:operator", details={"action": action})
    take_snapshot(inc_id, "infrastructure")

    try:
        audit.record_audit_event("applied", incident_id=inc_id, actor="human:operator", details={"action": action})
        res = core.apply_fix(action, diag, ev, use_mem)

        recovered = res.get("recovered", False)
        before_m = res.get("before", {})
        after_m = res.get("after", {})

        audit.record_audit_event(
            "verified",
            incident_id=inc_id,
            actor="system:verification",
            details={"recovered": recovered, "before": before_m, "after": after_m},
        )

        p95_before = before_m.get("p95_ms") or 0
        p95_after = after_m.get("p95_ms") or 0
        errors_after = after_m.get("errors", 0)

        # Auto-rollback if verification fails (errors>0 or p95 worse than threshold/before)
        if not recovered or errors_after > 0 or (p95_before > 0 and p95_after > p95_before and p95_after >= (200 if getattr(core, "FAST_MODE", False) else 500)):
            execute_rollback(
                inc_id,
                actor="system:auto_rollback",
                reason=f"Verification failed: {errors_after} errors, p95 {p95_after}ms",
            )
            res["recovered"] = False
            res["auto_rolled_back"] = True
        elif recovered:
            with watcher_lock:
                for inc in INCIDENTS:
                    if inc.get("status") == "detected":
                        inc["status"] = "resolved"
                        inc["resolved_at"] = time.time()
                        inc["fix_result"] = res

        return res
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
    proposal = codefix.propose(incident_id=incident_id)
    if "error" in proposal:
        raise HTTPException(400, proposal["error"])
    with codefix_lock:
        CURRENT_CODEFIX["proposal"] = proposal
        CURRENT_CODEFIX["approved"] = False
        CURRENT_CODEFIX["result"] = None

    # Evaluate policy for codefix
    pol = policy.evaluate_policy(
        action="codefix",
        confidence=0.90,
        is_codefix=True,
    )
    proposal["policy_decision"] = pol

    return proposal


@app.post("/api/codefix/approve")
def approve_codefix(body: dict = Body(default={})):
    proposal = body.get("proposal") or CURRENT_CODEFIX.get("proposal")
    if not proposal:
        raise HTTPException(400, "No active codefix proposal to approve.")

    inc_id = proposal.get("incident_id") or "inc-active"
    audit.record_audit_event("approved", incident_id=inc_id, actor="human:operator", details={"type": "codefix"})
    take_snapshot(inc_id, "codefix")

    try:
        audit.record_audit_event("applied", incident_id=inc_id, actor="human:operator", details={"type": "codefix"})
        res = codefix.approve(proposal)

        recovered = bool(res.get("recovered"))
        audit.record_audit_event("verified", incident_id=inc_id, actor="system:verification", details=res)

        if not recovered:
            execute_rollback(inc_id, actor="system:auto_rollback", reason="Codefix verification failed")
            res["auto_rolled_back"] = True

        with codefix_lock:
            CURRENT_CODEFIX["approved"] = True
            CURRENT_CODEFIX["result"] = res
        with watcher_lock:
            for inc in INCIDENTS:
                if inc.get("status") in ["detected", "codefix_proposed"]:
                    inc["status"] = "resolved" if recovered else "failed"
                    inc["resolved_at"] = time.time()
                    inc["codefix_result"] = res
        return res
    except Exception as e:
        execute_rollback(inc_id, actor="system:auto_rollback", reason=f"Exception during codefix: {e}")
        raise HTTPException(500, f"Code fix application failed: {e}")


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
