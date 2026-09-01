"""
actions/p2p_ledger.py
────────────────────────────────────────────────────────────────────────────────
Promise-to-Pay (P2P) Tracking Ledger.

Logs every dispatched promise_to_pay_nudge along with a simulated customer
follow-through outcome.

NOTE ON SIMULATION
──────────────────
Because this is a test-mode prototype without a live customer interactive
response channel, customer responses are simulated using plausible real-world
behavior distributions:
    - 60% 'promised_date_given'     (Customer clicked link and committed to a pay date)
    - 25% 'clicked_no_commitment'   (Customer opened link but did not complete commitment)
    - 15% 'no_response'             (Nudge delivered, message unopened or ignored)

These outcomes are strictly for demonstration and portfolio modeling purposes.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "p2p_ledger.json"

# Plausible outcome distribution
OUTCOME_CHOICES = ["promised_date_given", "clicked_no_commitment", "no_response"]
OUTCOME_WEIGHTS = [0.60, 0.25, 0.15]


def _ensure_ledger_file() -> None:
    """Ensure the ledger JSON file exists as a valid JSON array."""
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LEDGER_PATH.exists() or LEDGER_PATH.stat().st_size == 0:
        LEDGER_PATH.write_text("[]", encoding="utf-8")


def reset_ledger() -> None:
    """Reset the ledger to an empty list (useful before a fresh batch run)."""
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text("[]", encoding="utf-8")


def load_ledger() -> List[Dict[str, Any]]:
    """Load all entries from data/p2p_ledger.json."""
    _ensure_ledger_file()
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def record_nudge_dispatch(
    subscription_id: str,
    channel: str,
    mock_reference_id: str,
    amount: int,
    sent_timestamp: Optional[str] = None,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """
    Record a dispatched promise-to-pay nudge and simulate a plausible outcome.

    Parameters
    ----------
    subscription_id : str
        The subscription identifier (e.g. 'sub_XYZ')
    channel : str
        Communication channel ('whatsapp', 'sms', etc.)
    mock_reference_id : str
        Dispatched mock reference ID (e.g. 'wamsg_1234')
    amount : int
        Amount in paise
    sent_timestamp : Optional[str]
        ISO 8601 timestamp; defaults to now (UTC)
    rng : Optional[random.Random]
        Optional random generator for deterministic testing
    """
    _ensure_ledger_file()

    ts = sent_timestamp or datetime.now(timezone.utc).isoformat()
    r = rng or random.Random()

    # Sample outcome according to calibrated weights
    simulated_outcome = r.choices(OUTCOME_CHOICES, weights=OUTCOME_WEIGHTS, k=1)[0]

    # Additional simulated detail if date was promised
    promised_offset_days = r.randint(2, 5) if simulated_outcome == "promised_date_given" else None

    entry: Dict[str, Any] = {
        "subscription_id": subscription_id,
        "mock_reference_id": mock_reference_id,
        "channel": channel,
        "amount_paise": amount,
        "amount_inr": round(amount / 100, 2),
        "sent_timestamp": ts,
        "simulated_outcome": simulated_outcome,
        "promised_offset_days": promised_offset_days,
        "simulated_for_demo": True,
        "note": "SIMULATED OUTCOME: Generated via calibrated behavioral distribution for demo purposes.",
    }

    # Load, append, write back
    entries = load_ledger()
    entries.append(entry)
    LEDGER_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    return entry


def get_ledger_stats() -> Dict[str, Any]:
    """Calculate aggregate statistics across the P2P ledger."""
    entries = load_ledger()
    total = len(entries)
    if total == 0:
        return {
            "total_nudges": 0,
            "outcomes": {},
            "commitment_rate_pct": "0.0%",
            "total_value_addressed_inr": 0.0,
        }

    counts = Counter(e.get("simulated_outcome", "unknown") for e in entries)
    promised_count = counts.get("promised_date_given", 0)
    total_val = sum(e.get("amount_inr", 0.0) for e in entries)

    return {
        "total_nudges": total,
        "outcomes": dict(counts),
        "commitment_rate_pct": f"{(promised_count / total) * 100:.1f}%",
        "total_value_addressed_inr": round(total_val, 2),
    }
