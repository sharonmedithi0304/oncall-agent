"""CLI version of the loop: python agent/remediate.py"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
from diagnose import diagnose

if __name__ == "__main__":
    print("Breaking service...", core.break_service())
    ev, d = diagnose()
    print(json.dumps(d, indent=2))
    if input("\nApprove fix? [y/N] ").strip().lower() == "y":
        print(json.dumps(core.apply_fix(d["action"], d, ev, True), indent=2))
