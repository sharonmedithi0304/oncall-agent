import os, sys, shutil, subprocess, pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "agent"))

import codefix, memory

DEMO_APP = os.path.join(ROOT_DIR, "demo_app")
PRISTINE = os.path.join(DEMO_APP, "pristine", "checkout_logic.py")
TARGET = os.path.join(DEMO_APP, "checkout_logic.py")
BUGS = os.path.join(DEMO_APP, "bugs")


@pytest.fixture(autouse=True)
def restore_pristine():
    """Ensure pristine file is restored before and after each test."""
    if os.path.exists(PRISTINE):
        shutil.copyfile(PRISTINE, TARGET)
    yield
    if os.path.exists(PRISTINE):
        shutil.copyfile(PRISTINE, TARGET)


def test_buggy_variants_fail_and_pristine_passes():
    """Requirement: tests/test_checkout_logic.py must FAIL on each buggy variant and PASS on pristine."""
    # 1. Pristine passes
    shutil.copyfile(PRISTINE, TARGET)
    cmd = [sys.executable, "-m", "pytest", "tests/test_checkout_logic.py", "-v"]
    p_pristine = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True)
    assert p_pristine.returncode == 0, f"Pristine failed: {p_pristine.stdout}"

    # 2. Each bug variant must fail
    variants = ["discount_null.py", "off_by_one.py", "key_error.py"]
    for variant in variants:
        variant_path = os.path.join(BUGS, variant)
        assert os.path.exists(variant_path), f"Missing variant file: {variant_path}"
        shutil.copyfile(variant_path, TARGET)

        p_buggy = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True)
        assert p_buggy.returncode != 0, f"Bug variant {variant} unexpectedly passed tests!"


def test_safety_whitelist_rejects_external_paths():
    """Requirement: Safety whitelist only allows patching files inside demo_app/."""
    valid_diff = (
        "--- a/demo_app/checkout_logic.py\n"
        "+++ b/demo_app/checkout_logic.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-# old\n"
        "+# new\n"
    )
    assert codefix.validate_whitelist(valid_diff) is True

    unsafe_diffs = [
        "--- a/agent/api.py\n+++ b/agent/api.py\n@@ -1,1 +1,1 @@\n-# old\n+# new\n",
        "--- a/tests/test_faults.py\n+++ b/tests/test_faults.py\n@@ -1,1 +1,1 @@\n-# old\n+# new\n",
        "--- a/.git/config\n+++ b/.git/config\n@@ -1,1 +1,1 @@\n-# old\n+# new\n",
        "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1,1 +1,1 @@\n-# old\n+# new\n",
    ]
    for bad_diff in unsafe_diffs:
        with pytest.raises(ValueError, match="Safety violation"):
            codefix.validate_whitelist(bad_diff)


def test_offline_patcher_and_sandbox_verification():
    """Requirement: Offline patcher generates diffs that fix each variant and pass sandbox tests."""
    bug_classes = [
        ("discount_null.py", "AttributeError", "apply_discount"),
        ("off_by_one.py", "IndexError", "compute_volume_discount"),
        ("key_error.py", "KeyError", "get_tax_rate"),
    ]

    for variant_file, exc_type, func_name in bug_classes:
        variant_path = os.path.join(BUGS, variant_file)
        with open(variant_path, "r", encoding="utf-8") as f:
            buggy_content = f.read()

        patch_res = codefix._offline_patch(
            buggy_content, exc_type, func_name, "demo_app/checkout_logic.py"
        )
        assert patch_res["model_used"] == "offline-patcher"
        assert "diff" in patch_res
        assert len(patch_res["diff"]) > 0

        # Whitelist check
        assert codefix.validate_whitelist(patch_res["diff"]) is True

        # Copy buggy content into TARGET so sandbox copies it
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write(buggy_content)

        # Run sandbox test
        sb = codefix.run_sandbox_test(patch_res["diff"], "demo_app/checkout_logic.py")
        assert not sb["before"]["passed"], f"Expected sandbox before to fail for {variant_file}"
        assert sb["after"]["passed"], f"Expected sandbox after to pass for {variant_file}: {sb['after']['output']}"
        assert sb["verified"] is True


def test_codefix_memory_signature_and_recall():
    """Requirement: Save code-fix outcomes into memory so repeat bug can reuse earlier fix."""
    test_mem_path = "test_codefix_memory.json"
    memory.clear(test_mem_path)

    sig = memory.signature({
        "codefix": True,
        "exception": "AttributeError",
        "file": "demo_app/checkout_logic.py",
    })
    assert sig == "codefix|AttributeError|checkout_logic.py"

    dummy_diff = (
        "--- a/demo_app/checkout_logic.py\n"
        "+++ b/demo_app/checkout_logic.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-# bug\n"
        "+# fixed\n"
    )
    memory.remember({
        "signature": sig,
        "action": "codefix",
        "diff": dummy_diff,
        "worked": True,
        "file": "demo_app/checkout_logic.py",
        "exception": "AttributeError",
    }, path=test_mem_path)

    recalled = memory.recall({
        "codefix": True,
        "exception": "AttributeError",
        "file": "demo_app/checkout_logic.py",
    }, path=test_mem_path)

    assert len(recalled) == 1
    assert recalled[0]["signature"] == sig
    assert recalled[0]["diff"] == dummy_diff
    assert recalled[0]["worked"] is True

    memory.clear(test_mem_path)


def test_incident_report_generation():
    """Requirement: Write incident_reports/<incident-id>.md with timeline, root cause, diff, tests, metrics."""
    inc_id = "test-inc-12345"
    tb_info = {
        "file": "demo_app/checkout_logic.py",
        "line": 42,
        "exception": "AttributeError",
        "message": "'NoneType' object has no attribute 'rate'",
        "function": "apply_discount",
        "traceback": "Traceback (most recent call last):\n  File 'demo_app/checkout_logic.py', line 42",
        "timestamp": 1700000000.0,
    }
    patch_info = {
        "diff": "--- a/demo_app/checkout_logic.py\n+++ b/demo_app/checkout_logic.py\n",
        "explanation": "Added None check before accessing .rate attribute.",
        "model_used": "offline-patcher",
    }
    test_results = {
        "before": {"passed": False, "exit_code": 1, "output": "FAILED"},
        "after": {"passed": True, "exit_code": 0, "output": "4 passed"},
        "verified": True,
    }
    metrics = {
        "requests": 20,
        "errors": 0,
        "p95_ms": 28,
        "recovered": True,
    }
    git_info = {
        "branch": f"fix/{inc_id}",
        "committed": True,
        "pr_url": None,
        "compare_url": None,
    }

    report_path = codefix.write_incident_report(
        inc_id, tb_info, patch_info, test_results, metrics, git_info
    )
    assert os.path.exists(report_path)
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert inc_id in content
    assert "AttributeError" in content
    assert "demo_app/checkout_logic.py" in content
    assert "RECOVERED" in content

    # Cleanup test report
    if os.path.exists(report_path):
        os.remove(report_path)
