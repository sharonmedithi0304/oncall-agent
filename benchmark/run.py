import argparse
import csv
import json
import os
import random
import sys
import threading
import time
import urllib.request
from typing import Callable, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
AGENT_DIR = os.path.join(ROOT_DIR, "agent")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

import core
import collect
import diagnose as dg
import memory

ALL_FAULTS = [
    "pool_exhaustion",
    "slow_query",
    "bad_deploy",
    "memory_leak",
    "downstream_timeout",
    "cache_disabled",
]


def ensure_demo_server():
    """Ensure demo_app is running on port 8000, start in thread if not."""
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=1)
        if r.status == 200:
            return
    except Exception:
        pass

    import uvicorn
    from demo_app.main import app as demo_app
    config = uvicorn.Config(demo_app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(40):
        try:
            r = urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=1)
            if r.status == 200:
                break
        except Exception:
            time.sleep(0.1)


def run_benchmark(
    rounds: int = 3,
    seed: int = 42,
    fast: bool = True,
    output_json: str = "benchmark/results.json",
    output_csv: str = "benchmark/results.csv",
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    ensure_demo_server()

    if fast:
        core.set_fast_mode(True)

    # Prepare isolated memory paths
    memory_off_path = os.path.join(ROOT_DIR, "benchmark", "memory_off.json")
    memory_on_path = os.path.join(ROOT_DIR, "benchmark", "memory_on.json")
    memory.clear(path=memory_off_path)
    memory.clear(path=memory_on_path)

    # Generate identical shuffled schedule for both arms
    rng = random.Random(seed)
    schedule = []
    for round_idx in range(1, rounds + 1):
        round_faults = list(ALL_FAULTS)
        rng.shuffle(round_faults)
        for f in round_faults:
            schedule.append({"round": round_idx, "fault": f})

    total_tasks = len(schedule) * 2  # 2 arms
    completed_tasks = 0

    arms = [
        {"name": "memory_off", "use_memory": False, "mem_path": memory_off_path},
        {"name": "memory_on", "use_memory": True, "mem_path": memory_on_path},
    ]

    all_incidents = []

    for arm in arms:
        arm_name = arm["name"]
        use_mem = arm["use_memory"]
        mem_path = arm["mem_path"]

        for item in schedule:
            round_idx = item["round"]
            fault = item["fault"]

            if progress_callback:
                progress_callback({
                    "running": True,
                    "arm": arm_name,
                    "round": round_idx,
                    "fault": fault,
                    "completed": completed_tasks,
                    "total": total_tasks,
                    "percent": round((completed_tasks / total_tasks) * 100, 1),
                })

            # 1. Reset demo service fully before each incident
            core.reset()
            time.sleep(0.04)

            # 2. Inject fault and wait for detection
            t0 = time.time()
            core.break_service(fault)

            # 3. Diagnose and fix loop (max 3 attempts, excluding tried actions)
            tried_actions = []
            recovered = False
            attempts = 0

            for attempt in range(1, 4):
                attempts += 1
                ev, d = dg.diagnose(use_memory=use_mem, tried_actions=tried_actions, memory_path=mem_path)
                action = d.get("action")
                tried_actions.append(action)

                fix_res = core.apply_fix(action, d, ev, use_memory=use_mem, memory_path=mem_path)
                if fix_res.get("recovered"):
                    recovered = True
                    break
                time.sleep(0.03)

            secs = round(time.time() - t0, 2)
            wrong_attempts = (attempts - 1) if recovered else attempts
            first_try_success = bool(attempts == 1 and recovered)

            record = {
                "arm": arm_name,
                "round": round_idx,
                "fault": fault,
                "recovered": recovered,
                "attempts": attempts,
                "first_try_success": first_try_success,
                "wrong_attempts": wrong_attempts,
                "seconds_to_resolve": secs,
                "tried_actions": tried_actions,
            }
            all_incidents.append(record)
            completed_tasks += 1

    if fast:
        core.set_fast_mode(False)
    core.reset()

    # Calculate overall metrics per arm
    summary = {}
    for arm_name in ["memory_off", "memory_on"]:
        arm_items = [i for i in all_incidents if i["arm"] == arm_name]
        n = len(arm_items) or 1
        first_try_rate = round(sum(1 for i in arm_items if i["first_try_success"]) / n, 3)
        avg_wrong = round(sum(i["wrong_attempts"] for i in arm_items) / n, 2)
        avg_secs = round(sum(i["seconds_to_resolve"] for i in arm_items) / n, 2)
        summary[arm_name] = {
            "first_try_success_rate": first_try_rate,
            "avg_wrong_attempts": avg_wrong,
            "avg_seconds_to_recovery": avg_secs,
            "total_incidents": len(arm_items),
        }

    # Calculate per-round breakdown
    per_round = []
    for r in range(1, rounds + 1):
        r_entry = {"round": r}
        for arm_name in ["memory_off", "memory_on"]:
            r_items = [i for i in all_incidents if i["arm"] == arm_name and i["round"] == r]
            rn = len(r_items) or 1
            r_entry[arm_name] = {
                "first_try_success_rate": round(sum(1 for i in r_items if i["first_try_success"]) / rn, 3),
                "avg_wrong_attempts": round(sum(i["wrong_attempts"] for i in r_items) / rn, 2),
                "avg_seconds": round(sum(i["seconds_to_resolve"] for i in r_items) / rn, 2),
                "incidents": len(r_items),
            }
        per_round.append(r_entry)

    # Headline computed strictly from data
    off_w = summary["memory_off"]["avg_wrong_attempts"]
    on_w = summary["memory_on"]["avg_wrong_attempts"]
    off_t = summary["memory_off"]["avg_seconds_to_recovery"]
    on_t = summary["memory_on"]["avg_seconds_to_recovery"]

    wrong_cut = round(((off_w - on_w) / off_w) * 100) if off_w > 0 else 0
    time_cut = round(((off_t - on_t) / off_t) * 100) if off_t > 0 else 0

    headline = f"Memory cut wrong attempts by {wrong_cut}% and time-to-fix by {time_cut}%"
    summary["headline"] = headline

    results_data = {
        "timestamp": time.time(),
        "rounds": rounds,
        "seed": seed,
        "summary": summary,
        "per_round": per_round,
        "incidents": all_incidents,
    }

    # Write results.json
    out_json_path = os.path.join(ROOT_DIR, output_json) if not os.path.isabs(output_json) else output_json
    os.makedirs(os.path.dirname(os.path.abspath(out_json_path)), exist_ok=True)
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(results_data, f, indent=2)

    # Write results.csv
    out_csv_path = os.path.join(ROOT_DIR, output_csv) if not os.path.isabs(output_csv) else output_csv
    with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "arm",
            "round",
            "fault",
            "recovered",
            "attempts",
            "first_try_success",
            "wrong_attempts",
            "seconds_to_resolve",
            "tried_actions",
        ])
        for inc in all_incidents:
            writer.writerow([
                inc["arm"],
                inc["round"],
                inc["fault"],
                inc["recovered"],
                inc["attempts"],
                inc["first_try_success"],
                inc["wrong_attempts"],
                inc["seconds_to_resolve"],
                ";".join(inc["tried_actions"]),
            ])

    if progress_callback:
        progress_callback({
            "running": False,
            "completed": total_tasks,
            "total": total_tasks,
            "percent": 100.0,
            "results": results_data,
        })

    return results_data


def print_summary_table(res: dict):
    s = res["summary"]
    off = s["memory_off"]
    on = s["memory_on"]

    print("\n" + "=" * 65)
    print("                ON-CALL AGENT BENCHMARK RESULTS")
    print("=" * 65)
    print(f" Headline: {s.get('headline', '')}")
    print(f" Config:   {res['rounds']} rounds x 6 faults ({res['rounds']*6} incidents per arm), seed={res['seed']}")
    print("-" * 65)
    print(f"{'Metric':<28} | {'Memory OFF':<14} | {'Memory ON':<14}")
    print("-" * 65)
    print(f"{'First-try success rate':<28} | {off['first_try_success_rate']*100:>11.1f}% | {on['first_try_success_rate']*100:>11.1f}%")
    print(f"{'Average wrong attempts':<28} | {off['avg_wrong_attempts']:>13.2f} | {on['avg_wrong_attempts']:>13.2f}")
    print(f"{'Average recovery time':<28} | {off['avg_seconds_to_recovery']:>12.2f}s | {on['avg_seconds_to_recovery']:>12.2f}s")
    print("-" * 65)
    print("\nPer-Round Progression (First-try Success):")
    print(f"{'Round':<8} | {'Memory OFF':<14} | {'Memory ON':<14} | {'Wrong (OFF)':<12} | {'Wrong (ON)':<12}")
    print("-" * 65)
    for pr in res.get("per_round", []):
        r = pr["round"]
        o_rate = pr["memory_off"]["first_try_success_rate"] * 100
        m_rate = pr["memory_on"]["first_try_success_rate"] * 100
        o_w = pr["memory_off"]["avg_wrong_attempts"]
        m_w = pr["memory_on"]["avg_wrong_attempts"]
        print(f"Round {r:<2} | {o_rate:>12.1f}% | {m_rate:>12.1f}% | {o_w:>11.2f}  | {m_w:>11.2f}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark agent WITH vs WITHOUT memory.")
    parser.add_argument("--rounds", type=int, default=3, help="Number of rounds (default: 3)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for fault shuffling (default: 42)")
    parser.add_argument("--slow", action="store_true", help="Run with standard delays instead of fast mode")
    parser.add_argument("--output-json", default="benchmark/results.json", help="Path for results.json")
    parser.add_argument("--output-csv", default="benchmark/results.csv", help="Path for results.csv")

    args = parser.parse_args()
    results = run_benchmark(
        rounds=args.rounds,
        seed=args.seed,
        fast=not args.slow,
        output_json=args.output_json,
        output_csv=args.output_csv,
    )
    print_summary_table(results)

