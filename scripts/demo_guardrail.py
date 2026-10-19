"""Guardrail rejection demo: shows the risk gate refusing trades in real time.

Usage: ./venv/bin/python scripts/demo_guardrail.py
No network, no keys, no funds — pure risk-gate logic on display.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from src.execution import OrderRequest, RiskGate


def main():
    gate = RiskGate()
    ts = time.time()

    print("=== TARS Risk Gate — the compliance department ===\n")

    # 1. Oversized position: $50,000 against a $5,000 cap
    print("--- Attempt 1: trader wants $50,000 position (cap: $5,000) ---")
    order = OrderRequest(
        inst_id="BTC-USDT-SWAP", side="buy", order_type="market",
        size="50000", confidence_bps=9500, intended_price=110000.0,
    )
    result = gate.check_order(order, "trader-1",
                              current_price=110000.0, current_price_timestamp=ts)
    print(f"  VERDICT: {'APPROVED' if result.approved else 'REJECTED'}")
    print(f"  Reason:  {result.reason}\n")

    # 2. Within cap but below confidence floor
    print("--- Attempt 2: $1,000 trade, only 55% confidence (floor: 70%) ---")
    order = OrderRequest(
        inst_id="BTC-USDT-SWAP", side="buy", order_type="market",
        size="1000", confidence_bps=5500, intended_price=110000.0,
    )
    result = gate.check_order(order, "trader-1",
                              current_price=110000.0, current_price_timestamp=ts)
    print(f"  VERDICT: {'APPROVED' if result.approved else 'REJECTED'}")
    print(f"  Reason:  {result.reason}\n")

    # 3. Asset not on the allowlist
    print("--- Attempt 3: trade DOGE-USDT-SWAP (not on the allowlist) ---")
    order = OrderRequest(
        inst_id="DOGE-USDT-SWAP", side="buy", order_type="market",
        size="1000", confidence_bps=9000, intended_price=0.15,
    )
    result = gate.check_order(order, "trader-1",
                              current_price=0.15, current_price_timestamp=ts)
    print(f"  VERDICT: {'APPROVED' if result.approved else 'REJECTED'}")
    print(f"  Reason:  {result.reason}\n")

    # 4. Kill switch: one switch stops EVERYTHING
    print("--- Attempt 4: activate the kill switch, then try a valid trade ---")
    gate.activate_kill_switch("daily loss limit breached")
    order = OrderRequest(
        inst_id="BTC-USDT-SWAP", side="buy", order_type="market",
        size="100", confidence_bps=9500, intended_price=110000.0,
    )
    result = gate.check_order(order, "trader-1",
                              current_price=110000.0, current_price_timestamp=ts)
    print(f"  Kill switch: ACTIVE ({gate.kill_switch_status()['reason']})")
    print(f"  VERDICT: {'APPROVED' if result.approved else 'REJECTED'}")
    print(f"  Reason:  {result.reason}\n")

    print("=== The AI traders cannot override any of these. ===")
    print("=== On Wall Street, compliance outranks the traders. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
