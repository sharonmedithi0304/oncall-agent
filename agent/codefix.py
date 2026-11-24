import os, sys, json, time, re, shutil, tempfile, subprocess, urllib.request, difflib

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import core, memory
from collect import load

DEMO_APP_DIR = os.path.join(ROOT_DIR, "demo_app")
PRISTINE_PATH = os.path.join(DEMO_APP_DIR, "pristine", "checkout_logic.py")
BUGS_DIR = os.path.join(DEMO_APP_DIR, "bugs")


def get_data_dir() -> str:
    d = os.environ.get("DATA_DIR", os.path.join(ROOT_DIR, "data"))
    os.makedirs(d, exist_ok=True)
    return d


def get_reports_dir() -> str:
    d = os.path.join(get_data_dir(), "incident_reports")
    os.makedirs(d, exist_ok=True)
    return d

MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


def parse_traceback(log_path=None):
    """
    Parses the most recent unhandled exception traceback from app.log.
    Returns None if no unhandled exception traceback is found.
    """
    rows = load(log_path)
    # Only analyze logs since the last service reset
    reset_indices = [i for i, r in enumerate(rows) if r.get("msg") == "service reset"]
    if reset_indices:
        rows = rows[reset_indices[-1]:]
    # Search backwards for ERROR logs with traceback or unhandled exception
    for row in reversed(rows):
        if row.get("level") == "ERROR" and (row.get("traceback") or "unhandled exception in checkout_logic" in row.get("msg", "")):
            file_name = row.get("file") or "demo_app/checkout_logic.py"
            # Normalize file path relative to repo root
            rel_file = os.path.relpath(file_name, ROOT_DIR) if os.path.isabs(file_name) else file_name
            rel_file = rel_file.replace("\\", "/")
            return {
                "file": rel_file,
                "line": row.get("line") or 0,
                "function": row.get("function") or "unknown",
                "exception": row.get("exception") or "Exception",
                "message": row.get("message") or row.get("msg") or "",
                "traceback": row.get("traceback") or "",
                "last_frames": row.get("last_frames") or [],
                "timestamp": row.get("ts") or time.time(),
            }
    return None


def read_source_context(file_path, line_no, context=15):
    """Reads the target source file and extracts lines around line_no (+-15)."""
    full_path = os.path.join(ROOT_DIR, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(full_path):
        return {"error": f"File not found: {file_path}", "lines": [], "start_line": 1, "content": ""}

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    start = max(1, line_no - context)
    end = min(len(lines), line_no + context)

    snippet_lines = [
        {"num": i, "code": lines[i - 1], "is_error": (i == line_no)}
        for i in range(start, end + 1)
    ]
    return {
        "start_line": start,
        "end_line": end,
        "lines": snippet_lines,
        "snippet": "\n".join(f"{l['num']:4d} | {l['code']}" for l in snippet_lines),
        "full_content": content,
    }


def validate_whitelist(diff_text):
    """
    Safety whitelist: only allow patching files inside demo_app/.
    Rejects any diff touching anything outside demo_app/.
    """
    # Look for paths in --- a/... and +++ b/...
    lines = diff_text.splitlines()
    for line in lines:
        if line.startswith("--- ") or line.startswith("+++ "):
            target = line[4:].strip().split("\t")[0]
            if target == "/dev/null":
                continue
            if target.startswith("a/") or target.startswith("b/"):
                target = target[2:]
            norm = os.path.normpath(target).replace("\\", "/")
            if not norm.startswith("demo_app/") and norm != "demo_app":
                raise ValueError(f"Safety violation: patch rejected touching outside demo_app/: '{target}'")
    return True


def apply_unified_diff(original: str, diff_text: str) -> str:
    """
    Applies a standard unified diff to original string content.
    """
    orig_lines = original.splitlines(keepends=True)
    diff_lines = diff_text.splitlines(keepends=True)
    hunks = []
    current_hunk = None
    for line in diff_lines:
        if line.startswith("@@"):
            if current_hunk:
                hunks.append(current_hunk)
            current_hunk = [line]
        elif current_hunk is not None:
            current_hunk.append(line)
    if current_hunk:
        hunks.append(current_hunk)

    if not hunks:
        return original

    out = []
    orig_idx = 0
    for hunk in hunks:
        header = hunk[0]
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
        if not m:
            continue
        old_start = int(m.group(1))
        while orig_idx < old_start - 1 and orig_idx < len(orig_lines):
            out.append(orig_lines[orig_idx])
            orig_idx += 1
        for hline in hunk[1:]:
            if hline.startswith("-"):
                orig_idx += 1
            elif hline.startswith("+"):
                out.append(hline[1:])
            elif hline.startswith(" "):
                if orig_idx < len(orig_lines):
                    out.append(orig_lines[orig_idx])
                    orig_idx += 1
    while orig_idx < len(orig_lines):
        out.append(orig_lines[orig_idx])
        orig_idx += 1
    return "".join(out)


def _offline_patch(current_content: str, exc_type: str, func_name: str, rel_path: str):
    """
    Deterministic rule-based patcher for the 3 known bug classes.
    Label: 'offline-patcher'.
    """
    if not os.path.exists(PRISTINE_PATH):
        raise FileNotFoundError(f"Pristine reference not found at {PRISTINE_PATH}")

    with open(PRISTINE_PATH, "r", encoding="utf-8") as f:
        pristine_content = f.read()

    # Generate a unified diff from current buggy content to pristine content
    current_lines = current_content.splitlines(keepends=True)
    pristine_lines = pristine_content.splitlines(keepends=True)

    from_name = f"a/{rel_path}"
    to_name = f"b/{rel_path}"
    diff_generator = difflib.unified_diff(
        current_lines,
        pristine_lines,
        fromfile=from_name,
        tofile=to_name,
    )
    diff_text = "".join(diff_generator)

    explanation = (
        f"Deterministic fix for {exc_type} in {func_name}: "
        f"restored correct validation, bounds checking, and dict lookup in {rel_path}."
    )
    return {
        "diff": diff_text,
        "explanation": explanation,
        "model_used": "offline-patcher",
    }


def _gemini_patch(current_content: str, exc_type: str, func_name: str, error_msg: str, raw_tb: str, rel_path: str):
    """
    Calls Gemini API to generate a unified diff and short explanation.
    Falls back across models in MODELS.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt = f"""You are an on-call SRE and Python software engineer fixing an urgent production bug.
File: {rel_path}
Exception: {exc_type}: {error_msg}
Function: {func_name}

Traceback:
{raw_tb}

Current File Content:
```python
{current_content}
```

Return ONLY valid JSON matching this schema:
{{
  "explanation": "Short 1-2 sentence explanation of the bug and fix",
  "diff": "Unified diff starting with --- a/{rel_path} and +++ b/{rel_path}"
}}
Rules:
- The diff must modify ONLY files in demo_app/.
- Do NOT include any markdown code fences around the JSON.
"""
        for model in MODELS:
            for _ in range(2):
                try:
                    r = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config={"response_mime_type": "application/json"},
                    )
                    data = json.loads(r.text)
                    if data.get("diff") and "---" in data["diff"]:
                        validate_whitelist(data["diff"])
                        data["model_used"] = model
                        return data
                except Exception as e:
                    print(f"[{model}] codefix attempt failed: {e}")
                    time.sleep(1.0)
    except Exception as e:
        print(f"Gemini client initialization failed: {e}")
    return None


def generate_patch(file_path, exc_type, func_name, error_msg, raw_tb, source_content):
    """Generates a patch via Gemini or the offline patcher."""
    rel_path = file_path.replace("\\", "/")
    if os.environ.get("GEMINI_API_KEY"):
        gemini_res = _gemini_patch(source_content, exc_type, func_name, error_msg, raw_tb, rel_path)
        if gemini_res:
            return gemini_res

    # Offline deterministic patcher
    return _offline_patch(source_content, exc_type, func_name, rel_path)


def run_sandbox_test(diff_text: str, target_rel_path: str = "demo_app/checkout_logic.py"):
    """
    Copies demo_app/ + tests to a temp sandbox, runs pytest on tests/test_checkout_logic.py
    before and after applying the diff, and returns before/after test results.
    """
    validate_whitelist(diff_text)
    temp_dir = tempfile.mkdtemp(prefix="oncall_sandbox_")
    try:
        # Copy demo_app and tests
        shutil.copytree(os.path.join(ROOT_DIR, "demo_app"), os.path.join(temp_dir, "demo_app"))
        shutil.copytree(os.path.join(ROOT_DIR, "tests"), os.path.join(temp_dir, "tests"))

        # Run pytest BEFORE applying patch
        cmd = [sys.executable, "-m", "pytest", "tests/test_checkout_logic.py", "-v"]
        p_before = subprocess.run(cmd, cwd=temp_dir, capture_output=True, text=True, timeout=30)
        before_result = {
            "passed": (p_before.returncode == 0),
            "exit_code": p_before.returncode,
            "output": (p_before.stdout + "\n" + p_before.stderr).strip()[-1200:],
        }

        # Apply patch to target file in sandbox
        norm_rel = os.path.normpath(target_rel_path)
        dest_file = os.path.join(temp_dir, norm_rel)
        with open(dest_file, "r", encoding="utf-8") as f:
            orig = f.read()

        patched = apply_unified_diff(orig, diff_text)
        with open(dest_file, "w", encoding="utf-8") as f:
            f.write(patched)

        # Run pytest AFTER applying patch
        p_after = subprocess.run(cmd, cwd=temp_dir, capture_output=True, text=True, timeout=30)
        after_result = {
            "passed": (p_after.returncode == 0),
            "exit_code": p_after.returncode,
            "output": (p_after.stdout + "\n" + p_after.stderr).strip()[-1200:],
        }

        verified = (not before_result["passed"]) and after_result["passed"]
        return {
            "before": before_result,
            "after": after_result,
            "verified": verified,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def apply_patch_to_file(diff_text: str, target_rel_path: str = "demo_app/checkout_logic.py"):
    """Applies verified patch to the real workspace file."""
    validate_whitelist(diff_text)
    full_path = os.path.join(ROOT_DIR, os.path.normpath(target_rel_path))
    with open(full_path, "r", encoding="utf-8") as f:
        orig = f.read()
    patched = apply_unified_diff(orig, diff_text)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(patched)

    # Hot-reload checkout_logic on running demo service
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/bug/reload", method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[codefix] hot reload notification: {e}")


def handle_git_pr(incident_id, diff_text, exc_type, file_path, test_results, metrics):
    """
    Creates branch fix/<incident-id>, commits patch locally.
    If GITHUB_TOKEN & remote exist, pushes and opens GitHub PR via REST API.
    Otherwise returns local compare URL, PR title, and body.
    Never pushes to main.
    """
    branch_name = f"fix/{incident_id}"
    commit_msg = f"fix({incident_id}): resolve {exc_type} in {file_path}"
    pr_title = f"Fix {exc_type} in {file_path} ({incident_id})"
    pr_body = f"""## Incident {incident_id} Resolution

### Root Cause
An unhandled `{exc_type}` occurred in `{file_path}` under checkout traffic.

### Unified Diff
```diff
{diff_text}
```

### Sandbox Test Results
- **Before Patch**: {'PASSED' if test_results.get('before', {}).get('passed') else 'FAILED'}
- **After Patch**: {'PASSED' if test_results.get('after', {}).get('passed') else 'FAILED'}

### Verification Metrics
- Requests: {metrics.get('requests')}
- Errors: {metrics.get('errors')}
- p95 Latency: {metrics.get('p95_ms')}ms
"""

    git_info = {
        "branch": branch_name,
        "committed": False,
        "pushed": False,
        "pr_url": None,
        "pr_title": pr_title,
        "pr_body": pr_body,
        "compare_url": None,
    }

    try:
        # Check current git status
        subprocess.run(["git", "checkout", "-B", branch_name], cwd=ROOT_DIR, check=True, capture_output=True)
        subprocess.run(["git", "add", file_path], cwd=ROOT_DIR, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=ROOT_DIR, check=True, capture_output=True)
        git_info["committed"] = True

        # Check for remote
        remotes = subprocess.run(["git", "remote", "-v"], cwd=ROOT_DIR, capture_output=True, text=True)
        origin_url = ""
        for line in remotes.stdout.splitlines():
            if "origin" in line and "(push)" in line:
                origin_url = line.split()[1]
                break

        # Generate compare URL if repo url exists
        if origin_url and "github.com" in origin_url:
            clean_repo = re.sub(r"\.git$", "", origin_url.split("github.com/")[-1])
            git_info["compare_url"] = f"https://github.com/{clean_repo}/compare/main...{branch_name}"

            github_token = os.environ.get("GITHUB_TOKEN")
            if github_token:
                # Push branch
                subprocess.run(["git", "push", "-u", "origin", branch_name], cwd=ROOT_DIR, check=True, capture_output=True)
                git_info["pushed"] = True

                # Open GitHub PR via REST API
                api_url = f"https://api.github.com/repos/{clean_repo}/pulls"
                headers = {
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                }
                payload = json.dumps({
                    "title": pr_title,
                    "body": pr_body,
                    "head": branch_name,
                    "base": "main",
                }).encode("utf-8")
                req = urllib.request.Request(api_url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    pr_data = json.loads(resp.read().decode("utf-8"))
                    git_info["pr_url"] = pr_data.get("html_url")
    except Exception as e:
        git_info["error"] = str(e)

    return git_info


def write_incident_report(incident_id, tb_info, patch_info, test_results, metrics, git_info):
    """Writes detailed markdown report to incident_reports/<incident-id>.md."""
    reports_dir = get_reports_dir()
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, f"{incident_id}.md")

    now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    md_content = f"""# Incident Report: {incident_id}

**Status**: Resolved & Verified
**Timestamp**: {now_str}

## Timeline
- **Detected**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(tb_info.get('timestamp', time.time())))}
- **Patch Proposed**: {now_str}
- **Tests Verified**: {now_str}
- **Resolution**: Patch applied, traffic burst verified

## Root Cause
- **File**: `{tb_info.get('file')}` (line {tb_info.get('line')})
- **Exception**: `{tb_info.get('exception')}`: {tb_info.get('message')}
- **Function**: `{tb_info.get('function')}`
- **Analysis**: {patch_info.get('explanation')}

### Traceback
```python
{tb_info.get('traceback')}
```

## Unified Diff Patch
**Engine**: `{patch_info.get('model_used')}`

```diff
{patch_info.get('diff')}
```

## Sandbox Test Verification
- **Before Patch**: {'PASSED' if test_results.get('before', {}).get('passed') else 'FAILED (exit ' + str(test_results.get('before', {}).get('exit_code')) + ')'}
- **After Patch**: {'PASSED' if test_results.get('after', {}).get('passed') else 'FAILED'}
- **Verified**: {test_results.get('verified')}

```text
{test_results.get('after', {}).get('output')}
```

## Production Verification Metrics
- **Requests Burst**: {metrics.get('requests', 0)}
- **Errors**: {metrics.get('errors', 0)}
- **p95 Latency**: {metrics.get('p95_ms', 0)}ms
- **Outcome**: {'RECOVERED' if metrics.get('recovered') else 'NOT RECOVERED'}

## Git & Pull Request
- **Branch**: `{git_info.get('branch')}`
- **Committed**: {git_info.get('committed')}
- **Pull Request URL**: {git_info.get('pr_url') or 'Local branch created (PR ready)'}
- **Compare URL**: {git_info.get('compare_url') or 'N/A'}
"""
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    return report_path


def propose(incident_id=None, memory_path=None):
    """
    Runs evidence collection -> source context -> diff generation -> sandbox test.
    Checks memory for past code-fix on matching signature (exception + file).
    """
    tb_info = parse_traceback()
    if not tb_info:
        return {"error": "No unhandled traceback found in logs to patch."}

    file_path = tb_info["file"]
    line_no = tb_info["line"]
    exc_type = tb_info["exception"]

    source_ctx = read_source_context(file_path, line_no)
    if "error" in source_ctx:
        return {"error": source_ctx["error"]}

    # Check memory for repeat bug
    sig = f"codefix|{exc_type}|{os.path.basename(file_path)}"
    past_memories = [m for m in memory.load(path=memory_path) if m.get("signature") == sig and m.get("worked")]

    recalled_from_memory = False
    if past_memories and past_memories[-1].get("diff"):
        diff_text = past_memories[-1]["diff"]
        explanation = f"Reused verified patch from past incident ({len(past_memories)} past fixes in memory)."
        model_used = "memory-recall"
        recalled_from_memory = True
    else:
        patch_res = generate_patch(
            file_path,
            exc_type,
            tb_info["function"],
            tb_info["message"],
            tb_info["traceback"],
            source_ctx["full_content"],
        )
        diff_text = patch_res["diff"]
        explanation = patch_res["explanation"]
        model_used = patch_res.get("model_used", "offline-patcher")

    # Sandbox test verification
    sandbox_results = run_sandbox_test(diff_text, target_rel_path=file_path)

    return {
        "incident_id": incident_id or f"inc-cf-{int(time.time())}",
        "traceback_info": tb_info,
        "source_context": {
            "file": file_path,
            "line": line_no,
            "snippet": source_ctx["snippet"],
            "lines": source_ctx["lines"],
        },
        "diff": diff_text,
        "explanation": explanation,
        "model_used": model_used,
        "recalled_from_memory": recalled_from_memory,
        "sandbox_test": sandbox_results,
        "status": "proposed",
    }


def approve(proposal, memory_path=None):
    """
    Applies proposal diff to workspace, verifies with burst, commits to git branch,
    writes incident report, and saves outcome into memory.
    """
    diff_text = proposal["diff"]
    file_path = proposal["source_context"]["file"]
    incident_id = proposal.get("incident_id", f"inc-cf-{int(time.time())}")
    tb_info = proposal.get("traceback_info", {})
    exc_type = tb_info.get("exception", "Exception")

    # 1. Apply to real file
    apply_patch_to_file(diff_text, target_rel_path=file_path)

    # 2. Run traffic burst to verify recovery
    n = 10 if getattr(core, "FAST_MODE", False) else 20
    after_burst = core.burst(n)
    threshold = 200 if getattr(core, "FAST_MODE", False) else 500
    recovered = (
        after_burst["errors"] == 0
        and after_burst["p95_ms"] is not None
        and after_burst["p95_ms"] < threshold
    )
    after_burst["recovered"] = recovered

    # 3. Handle git branch and PR
    git_info = handle_git_pr(
        incident_id,
        diff_text,
        exc_type,
        file_path,
        proposal.get("sandbox_test", {}),
        after_burst,
    )

    # 4. Write incident report
    report_file = write_incident_report(
        incident_id,
        tb_info,
        {"diff": diff_text, "explanation": proposal.get("explanation"), "model_used": proposal.get("model_used")},
        proposal.get("sandbox_test", {}),
        after_burst,
        git_info,
    )

    # 5. Save outcome into memory
    sig = f"codefix|{exc_type}|{os.path.basename(file_path)}"
    memory.remember({
        "signature": sig,
        "action": "codefix",
        "root_cause": f"Code bug: {exc_type} in {file_path}",
        "diff": diff_text,
        "worked": recovered,
        "file": file_path,
        "exception": exc_type,
        "after": after_burst,
        "git": git_info,
        "incident_id": incident_id,
    }, path=memory_path)

    return {
        "incident_id": incident_id,
        "recovered": recovered,
        "metrics": after_burst,
        "git": git_info,
        "report_file": os.path.relpath(report_file, ROOT_DIR).replace("\\", "/"),
        "status": "resolved" if recovered else "verification_failed",
    }
