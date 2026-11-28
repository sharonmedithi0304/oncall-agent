import os
from typing import Dict, Any, Optional

def generate_pr_body(
    proposal_or_incident_id: Any = None,
    summary: str = "",
    root_cause: str = "",
    stack_trace_loc: Optional[Dict[str, Any]] = None,
    diff_text: str = "",
    sandbox_results: Optional[Dict[str, Any]] = None,
    verification_metrics: Optional[Dict[str, Any]] = None,
    policy_decision: Optional[Dict[str, Any]] = None,
    incident_report_id: Optional[str] = None,
    audit_chain_hash: Optional[str] = None,
    rollback_instructions: Optional[str] = None,
    incident_id: Optional[str] = None,
    **kwargs,
) -> str:
    """
    Generates a structured markdown Pull Request body containing all diagnostic,
    sandbox test, verification, policy, and cryptographic audit trail information.
    Accepts either individual parameters or a proposal dictionary as the first argument.
    """
    if isinstance(proposal_or_incident_id, dict):
        prop = proposal_or_incident_id
        actual_incident_id = incident_id or kwargs.get("incident_id") or prop.get("incident_id", "inc-unknown")
        summary = summary or prop.get("summary", "Automated code remediation")
        root_cause = root_cause or prop.get("root_cause") or prop.get("summary", "Exception encountered")
        stack_trace_loc = stack_trace_loc or prop.get("traceback_info", {})
        diff_text = diff_text or prop.get("diff", "")
        sandbox_results = sandbox_results or prop.get("sandbox_test", {})
        verification_metrics = verification_metrics or prop.get("verification_metrics", {})
        policy_decision = policy_decision or prop.get("policy_decision", {})
    else:
        actual_incident_id = incident_id or proposal_or_incident_id or "inc-active"

    incident_id = actual_incident_id
    stack_trace_loc = stack_trace_loc or {}
    sandbox_results = sandbox_results or {}
    verification_metrics = verification_metrics or {}
    file_path = stack_trace_loc.get("file", "unknown")
    line_no = stack_trace_loc.get("line", "unknown")
    exc_type = stack_trace_loc.get("exception", "Exception")
    func_name = stack_trace_loc.get("function", "unknown")
    tb = stack_trace_loc.get("traceback") or "No traceback provided"

    # Sandbox tests
    before_test = sandbox_results.get("before", {})
    after_test = sandbox_results.get("after", {})
    before_passed = before_test.get("passed", False)
    after_passed = after_test.get("passed", False)
    before_status = "PASSED" if before_passed else f"FAILED (exit code {before_test.get('exit_code', 1)})"
    after_status = "PASSED" if after_passed else f"FAILED (exit code {after_test.get('exit_code', 1)})"
    sandbox_out = after_test.get("output") or sandbox_results.get("output") or "Sandbox test suite executed."

    # Production metrics
    before_m = verification_metrics.get("before", {})
    after_m = verification_metrics.get("after", verification_metrics)
    before_p95 = before_m.get("p95_ms", 0)
    before_errs = before_m.get("errors", 0)
    after_p95 = after_m.get("p95_ms", 0)
    after_errs = after_m.get("errors", 0)
    recovered = bool(verification_metrics.get("recovered", after_errs == 0))

    # Policy
    pol = policy_decision or {}
    decision = pol.get("decision", "NEEDS_APPROVAL")
    risk_tier = pol.get("risk_tier", "high")
    reasons = pol.get("reasons", ["High-risk code modification requiring review."])
    reasons_md = "\n".join(f"- {r}" for r in reasons)

    # Rollback instructions
    default_rollback = (
        f"1. **Dashboard**: Click 'Manual Rollback' on incident `{incident_id}`.\n"
        f"2. **Agent API**: Call `POST /api/rollback` with `{{\"incident_id\": \"{incident_id}\"}}`.\n"
        f"3. **Git CLI**: Revert the fix branch `fix/{incident_id}` with `git revert -m 1 <merge-commit>` or revert PR."
    )
    rollback_text = rollback_instructions or default_rollback

    report_id = incident_report_id or incident_id
    chain_hash = audit_chain_hash or "genesis"

    body = f"""## Summary
{summary}

## Root Cause
{root_cause}

## Stack Trace Location
- **File**: `{file_path}`
- **Line**: {line_no}
- **Function**: `{func_name}`
- **Exception**: `{exc_type}`

```python
{tb.strip()}
```

## Unified Diff Summary
```diff
{diff_text.strip()}
```

## Sandbox Test Results Before/After
- **Before Patch**: {before_status}
- **After Patch**: {after_status}

<details>
<summary>Sandbox Pytest Output</summary>

```text
{sandbox_out.strip()}
```
</details>

## Production Verification Metrics
- **Before Fix**: p95 = {before_p95}ms, errors = {before_errs}
- **After Fix**: p95 = {after_p95}ms, errors = {after_errs}
- **Status**: {'RECOVERED' if recovered else 'NOT RECOVERED'}

## Policy Decision
- **Decision**: `{decision}`
- **Risk Tier**: `{risk_tier}`
- **Reasons**:
{reasons_md}

## Incident Report
- **Incident ID / Report**: `{report_id}`
- **Report Path**: `incident_reports/{incident_id}.md`

## Audit Chain Head Hash
- **SHA-256 Hash**: `{chain_hash}`

## Rollback Instructions
{rollback_text}
"""
    return body.strip()
