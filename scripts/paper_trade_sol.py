#!/usr/bin/env python3
"""Paper-trade SOL funding_persistence_z — dry_run, no capital."""

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.agent import AutonomousTradingAgent
from src.execution import RiskGate
from src.execution.risk_gate import DurableDailyCounters
from src.okx_cli import OkxCli, OkxCliConfig
from src.audit_trail import AuditLog
from src.curator import CuratorAgent

async def main():
    cli = OkxCli(OkxCliConfig(demo=True))
    # Durable counters disabled for paper — isolated
    risk_gate = RiskGate(
        max_position_usd=5000,
        max_daily_loss_usd=500,
        max_daily_trades=10,
        min_confidence_bps=7000,
        counters_durable=False,
        counter_store=DurableDailyCounters(enabled=False),
    )
    curator = CuratorAgent(audit_log=None)
    audit_log = AuditLog(path=str(REPO / "audit_log.jsonl"))

    agent = AutonomousTradingAgent(
        okx_cli=cli,
        risk_gate=risk_gate,
        onchain_logger=None,  # no onchain for paper
        dry_run=True,
        curator=curator,
        audit_log=audit_log,
        expected_equity=None,
    )

    print("Starting paper-trade cycle for SOL-USDT-SWAP (dry_run=True)...")
    result = await agent.run_trading_cycle(["SOL-USDT-SWAP"])
    print(f"\nCycle {result.cycle_id} status={result.status}")
    print(f"Signals: {len(result.signals)}")
    for s in result.signals:
        ens = s.get("ensemble", {})
        print(f"  ensemble: {ens.get('direction')} conf {ens.get('confidence_bps')} rationale: {ens.get('rationale','')[:120]}")
        for ind in s.get("individual", []):
            print(f"    - {ind['strategy']}: {ind['direction']} {ind['confidence_bps']} | {ind['rationale'][:100]}")
    print(f"Decisions: {len(result.decisions)}")
    for d in result.decisions:
        print(f"  {d}")
    print(f"Executions: {len(result.executions)}")
    for e in result.executions:
        print(f"  {e}")
    print(f"Errors: {result.errors}")
    print(f"Curator: {result.curator}")
    print("\nPaper-trade done — no capital moved (dry_run).")

if __name__ == "__main__":
    asyncio.run(main())
