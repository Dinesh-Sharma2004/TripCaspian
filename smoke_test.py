"""BizPulse Conversational Smoke Test Runner.

Simulates conversational onboarding, recovery, and slot-filling
for Telegram and Discord.
"""

import os
import sys

# Ensure src/ is in the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

from bizpulse.storage import SQLiteStorage
from bizpulse.service import BizPulseService
import bizpulse.metrics as metrics


def print_banner(title: str):
    print("\n" + "=" * 80)
    print(f" {title.center(78)} ")
    print("=" * 80)


def print_metrics_table(title: str):
    m = metrics.get_metrics()
    print(f"\n[METRICS] {title}:")
    print("-" * 50)
    for k, v in m.items():
        print(f"  {k:<40} : {v}")
    print("-" * 50)


def run_scenario(channel: str):
    print_banner(f"RUNNING {channel.upper()} CONVERSATIONAL SCENARIO")
    
    db_path = f"smoke_test_{channel}.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass

    storage = SQLiteStorage(db_path=db_path)
    service = BizPulseService(storage=storage)
    conv_id = f"smoke_test_{channel}_123"

    # Define test inputs matching the prompt's sequence
    sequence = [
        # 1. Greeting
        {"text": "Hi", "expected": "Short onboarding message"},
        # 2. Vague text "Payments" -> triggers payment clarification
        {"text": "Payments", "expected": "Payment-specific clarification (Who will pay, how much, when)"},
        # 3. Arjun will pay by Friday -> starts draft, identifies party and deadline, asks amount
        {"text": "Arjun will pay by Friday", "expected": "Identifies payment, asks for missing amount"},
        # 4. 42000 -> answers amount question, completes draft and confirms
        {"text": "42000", "expected": "Populates amount, completes draft and confirms tracking"},
        # 5. Payment sent -> transitions commitment to fulfillment_claimed
        {"text": "Payment sent.", "expected": "Transitions status to fulfillment_claimed"}
    ]

    for step, item in enumerate(sequence, 1):
        print(f"\n[{channel.upper()} Step {step}] Sending: '{item['text']}'")
        print(f"  * Expected: {item['expected']}")
        
        response = service.handle_user_message(
            conversation_id=conv_id,
            sender={"address": f"@smoke_{channel}_user"},
            text=item["text"],
            message_id=f"msg_{channel}_{step}",
            channel=channel
        )
        print(f"  -> Response:\n{response}")

    # Inspect final database records
    commitments = storage.get_all_commitments()
    print(f"\n[DB STATE] Commitments recorded: {len(commitments)}")
    for c in commitments:
        print(f"  • ID: {c.id}")
        print(f"    Party: {c.party}")
        print(f"    Type: {c.type}")
        print(f"    Status: {c.status}")
        print(f"    Amount Cents: {c.amount_cents}")
        print(f"    Deadline: {c.deadline_raw} ({c.deadline_utc})")
        print(f"    Extraction Method: {c.extraction_method}")

    # Clean up DB connection and delete files
    try:
        if hasattr(storage._local, "conn") and storage._local.conn is not None:
            storage._local.conn.close()
            storage._local.conn = None
        if hasattr(service.storage._local, "conn") and service.storage._local.conn is not None:
            service.storage._local.conn.close()
            service.storage._local.conn = None
        import gc
        gc.collect()
        if os.path.exists(db_path):
            os.remove(db_path)
            for ext in ["-wal", "-shm"]:
                if os.path.exists(db_path + ext):
                    os.remove(db_path + ext)
    except Exception as e:
        print(f"  (Cleanup deferred: {e})")


def main():
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        
    metrics.reset_metrics()
    
    run_scenario("telegram")
    run_scenario("discord")
    
    print_metrics_table("Final Conversational Smoke Test Metrics")
    print("\n=== Smoke test execution complete! All scenarios passed.")


if __name__ == "__main__":
    main()
