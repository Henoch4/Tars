#!/usr/bin/env python3
"""Paper-trade SOL funding_persistence_z — dry_run, no capital."""

import asyncio
import json
import random
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Stable paper-harness paths and cost constants. The full historical harness
# remains a separate repair item; these exports keep reports/imports explicit.
STATE_PATH = REPO / "data" / "paper_sol_state.json"
LEDGER_PATH = REPO / "data" / "paper_sol_ledger.jsonl"
AUDIT_PATH = REPO / "data" / "paper_sol_audit.jsonl"
FEE_BPS = 5.0
SLIP_BPS = 3.0
FEE_FRAC = FEE_BPS / 10_000
SLIP_FRAC = SLIP_BPS / 10_000
BREAK_EVEN_FUNDING = 0.00030476190476190474
MAX_TRADES = 30
MAX_HOLD_PERIODS = 21
PERIOD_S = 8 * 3600

def draw_slippage(rng=random): return abs(rng.gauss(SLIP_FRAC / 2, SLIP_FRAC / 2))
def adverse_fill_price(reference, side, slippage): return reference * (1 + slippage) if side.lower() == "buy" else reference * (1 - slippage)
def accrue_funding(position, rate, elapsed_s):
    amount = float(position["size_usd"]) * float(rate) * min(max(float(elapsed_s), 0) / PERIOD_S, 1.0) * (-1 if position.get("direction") == "LONG" else 1)
    position["funding_accrued_usd"] = position.get("funding_accrued_usd", 0.0) + amount
    return amount
def close_position(state, position, reference_price, hold_s, reason, rng=random):
    direction = position["direction"]; slip = draw_slippage(rng); side = "sell" if direction == "LONG" else "buy"; exit_price = adverse_fill_price(reference_price, side, slip); size = float(position["size_usd"])
    gross = ((exit_price-position["entry_price"]) / position["entry_price"] * size if direction == "LONG" else (position["entry_price"]-exit_price) / position["entry_price"] * size)
    exit_fee = size * FEE_FRAC; entry_fee = position.get("entry_fee_usd", size * FEE_FRAC)
    trade = {"event":"close","asset":position.get("asset","SOL-USDT-SWAP"),"direction":direction,"entry_price":position["entry_price"],"exit_price":exit_price,"size_usd":size,"entry_fee_usd":entry_fee,"exit_fee_usd":exit_fee,"entry_slippage_bps":position.get("entry_slippage_bps",SLIP_BPS),"exit_slippage_bps":slip*1e4,"funding_accrued_usd":position.get("funding_accrued_usd",0.0),"gross_pnl_usd":gross,"net_pnl_usd":gross-entry_fee-exit_fee+position.get("funding_accrued_usd",0.0),"reason":reason,"hold_s":hold_s}
    state["open_position"] = None; state.setdefault("closed_trades", []).append(trade); state["net_carry_usd"] = sum(t.get("net_pnl_usd",0.0) for t in state["closed_trades"])
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True); LEDGER_PATH.open("a", encoding="utf-8").write(json.dumps(trade)+"\n"); return trade
def update_funding_history(state, row):
    pid = row.get("period_id") if isinstance(row, dict) else None
    if not pid or state.get("last_period_id") == pid: return False
    state.setdefault("funding_history", []).append(float(row.get("settled_rate") if row.get("settled_rate") is not None else row.get("funding_rate"))); state["last_period_id"] = pid; return True
def extract_funding_row(payload):
    if not isinstance(payload,list) or not payload or not isinstance(payload[0],dict): return None
    row=payload[0]
    try: rate=float(row["fundingRate"])
    except (KeyError,TypeError,ValueError): return None
    return {"funding_rate":rate,"period_id":str(row["fundingTime"]) if row.get("fundingTime") is not None else None,"settled_rate":float(row["settFundingRate"]) if row.get("settFundingRate") not in (None,"") else None}
def clears_break_even(rate, direction): return float(rate) >= BREAK_EVEN_FUNDING if direction == "SHORT" else float(rate) <= -BREAK_EVEN_FUNDING
def btc_regime_snapshot(row):
    if not isinstance(row,dict): return None
    try: price=float(row.get("btc_price")); raw=row.get("candles",[]); candles=raw.get("data",[]) if isinstance(raw,dict) else raw; high=max(float(c[2]) for c in candles if len(c)>2)
    except (TypeError,ValueError,KeyError): return None
    return None if price<=0 or high<=0 else {"btc_price_usd":price,"btc_high90":high,"btc_drawdown_from_90d_high":price/high-1}
def save_state(state): STATE_PATH.parent.mkdir(parents=True,exist_ok=True); STATE_PATH.write_text(json.dumps(state,indent=2),encoding="utf-8")
def load_state(): return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {"funding_history":[],"price_history":[],"open_position":None,"closed_trades":[],"net_carry_usd":0.0,"cycle_count":0}

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
