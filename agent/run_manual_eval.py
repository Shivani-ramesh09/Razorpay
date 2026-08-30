"""
agent/run_manual_eval.py
────────────────────────────────────────────────────────────────────────────────
Runs the standalone LLM agent against 15 hand-crafted test cases and prints
the proposed actions and reasoning for manual review.

Usage:
    python agent/run_manual_eval.py
"""

import json
import sys
from pathlib import Path
from dataclasses import asdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schema.subscription_schema import SubscriptionRecord
from agent.llm_agent import propose

TEST_CASES_PATH = ROOT / "agent" / "test_cases_manual.json"


def main():
    if not TEST_CASES_PATH.exists():
        print(f"Error: {TEST_CASES_PATH} not found.")
        sys.exit(1)

    cases = json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(cases)} manual test cases.\n")

    for case in cases:
        print("=" * 80)
        print(f"CASE {case['id']}: {case['scenario']}")
        print("-" * 80)

        raw = case["record"]
        raw["subscription_id"] = "sub_" + raw["subscription_id"][4:].replace("_", "")
        raw.setdefault("customer_id", "cust_dummy1")
        raw.setdefault("plan_id", "plan_dummy1")
        raw.setdefault("paid_count", 1)
        rem = raw.get("remaining_count", 0)
        raw.setdefault("total_count", rem + 2)
        raw.setdefault("above_15k_threshold", raw.get("amount", 0) >= 1500000)
        
        record = SubscriptionRecord(**raw)
        context = case.get("context", {})

        print(f"Input Bucket : {record.failure_bucket}")
        if "predicted_optimal_offset_hours" in context:
            print(f"Predicted Opt: {context['predicted_optimal_offset_hours']}h")
        if getattr(record, "opt_out", False):
            print(f"Opt Out      : True")
            
        print("\nRequesting LLM proposal...")
        try:
            proposal = propose(record, context)
            print("\nOutput (JSON):")
            print(json.dumps(asdict(proposal), indent=2))
            
            # Simple validation to flag missing fields
            required_fields = {"proposed_action", "proposed_channel", "reasoning", "confidence"}
            actual_fields = set(asdict(proposal).keys())
            if not required_fields.issubset(actual_fields):
                print(f"\n[WARNING] Missing required fields. Found: {actual_fields}")
                
        except Exception as e:
            print(f"\n[ERROR] Failed to get proposal: {e}")
            
        print("=" * 80 + "\n")
        
        # Avoid free-tier rate limit (5 RPM) for gemini-3.5-flash
        import time
        time.sleep(13)

if __name__ == "__main__":
    main()
