# OnCall AI: autonomous on-call engineer

Breaks a real service, finds the root cause from logs, asks for approval, applies a fix, and proves recovery with numbers. Remembers failed fixes so it never repeats them.

## Supported Faults & Remediation

| Fault | Symptom | Right Fix | Plausible Wrong Fix | Description |
|---|---|---|---|---|
| `pool_exhaustion` | 503 errors (`QueuePool limit reached`), high p95 | `rollback_config` | `scale_pool` | DB connections held too long (2s) |
| `slow_query` | Elevated p95 latency (0.8-1.5s), 0 errors | `add_index` | `restart_service`, `scale_pool` | Missing database index causes table scan |
| `bad_deploy` | ~40% 500 errors (`NullReference in apply_discount`), v3.8.3 | `rollback_deploy` | `restart_service` | Buggy release deployed, flips version to v3.8.3 |
| `memory_leak` | Latency creeps up, log `memory 85% of limit` | `restart_service` | `scale_pool` | Heap buffer grows on each request |
| `downstream_timeout` | 504 errors (`payment-gateway timeout`), 3s delay | `enable_circuit_breaker` | `restart_service` | Downstream payment provider hangs |
| `cache_disabled` | High p95 latency (0.6-0.8s), 0 errors | `enable_cache` | `scale_pool` | Cache layer disabled, all requests hit DB |

## Run (Windows)
1. Install Node.js (LTS) from nodejs.org if you don't have it.
2. (Optional, real LLM) in cmd: `set GEMINI_API_KEY=your_key`
3. In the same cmd, from this folder: `run.bat`
4. Browser opens at http://localhost:5173

Without a key the agent uses an offline heuristic fallback so the demo still runs (shown in the sidebar).

## Run Tests
```bash
pytest tests/test_faults.py -v
```

## Demo flow
1. Select a fault from the dropdown (e.g. Pool Exhaustion, Memory Leak, or Slow Query) and click **Break service**.
2. Alternatively, let traffic run—the background watcher auto-detects sustained degradation (3 consecutive polls) and opens an incident with diagnosis ready.
3. Diagnose with **Memory OFF**, approve: it tries a plausible first-principles fix (e.g. `scale_pool` or `restart_service`), which fails to recover. Saved to memory.
4. Diagnose with **Memory ON**: it recalls the failed attempt, avoids it, and applies the matching fix (e.g. `rollback_config`, `add_index`, `enable_cache`), proving recovery with live p95 and error metrics.

## Layout
- `demo_app/`   checkout service with 6 realistic faults and fix endpoints (port 8000)
- `agent/`      collect (logs to evidence), diagnose (LLM + offline fallback), memory, core (apply + verify), api (port 8001) with auto-detection watcher
- `dashboard/`  React + Tailwind + Framer Motion + Lucide UI with fault dropdown & incident alert (port 5173)
- `tests/`      Pytest test suite verifying recovery and signatures for all 6 faults
