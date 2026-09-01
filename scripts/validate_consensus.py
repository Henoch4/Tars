#!/usr/bin/env python3
"""
Consensus validation — A/B test: ConsensusGate vs ensemble baseline.

Runs both decision engines on historical data and compares OOS Sharpe/Calmar.
Per design doc §4.3: "does requiring consensus improve out-of-sample 
Sharpe/Calmar versus the current plain ensemble vote, or does it just 
reduce trade frequency and look more 'disciplined' while adding no information?"
"""
from __future__ import annotations

import asyncio
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

# Repo root
REPO = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(REPO))

from src.signals import (
    Signal,
    mean_reversion_signal,
    momentum_signal,
    funding_rate_signal,
    ensemble_signal,
    backtest_simple,
)
from src.trader import (
    MarketContext,
    MeanReversionTrader,
    MomentumTrader,
    FundingRateTrader,
    FundingCarryTrader,
    create_default_swarm,
)
from src.consensus import ConsensusGate, ConsensusBehavior, create_default_consensus_gate


@dataclass
class ValidationResult:
    """Results of a validation run."""
    name: str
    total_return_bps: int
    sharpe_ratio: float
    max_drawdown_bps: int
    calmar_ratio: float
    num_trades: int
    win_rate: float
    consensus_reached_pct: float = 0.0


def load_price_data(symbol: str) -> list[float]:
    """Load historical price data for a symbol."""
    # Map symbol to file name
    base = symbol.replace("-USDT-SWAP", "").replace("-USDT", "")
    csv_path = REPO / "data" / f"{base}_1h_candles.csv"
    if not csv_path.exists():
        # Try carry data
        csv_path = REPO / "data" / "carry" / f"{base}_perp_1h.csv"
    if not csv_path.exists():
        return []
    df = pd.read_csv(csv_path)
    # Handle different column names
    if "close" in df.columns:
        return df["close"].astype(float).tolist()
    elif "c" in df.columns:
        return df["c"].astype(float).tolist()
    elif "Close" in df.columns:
        return df["Close"].astype(float).tolist()
    return []


def load_price_data_dicts(symbol: str) -> list[dict]:
    """Load price data as list of dicts for momentum signal."""
    base = symbol.replace("-USDT-SWAP", "").replace("-USDT", "")
    csv_path = REPO / "data" / f"{base}_1h_candles.csv"
    if not csv_path.exists():
        csv_path = REPO / "data" / "carry" / f"{base}_perp_1h.csv"
    if not csv_path.exists():
        return []
    df = pd.read_csv(csv_path)
    # Handle different column names
    close_col = "close" if "close" in df.columns else ("c" if "c" in df.columns else "Close")
    vol_col = "vol" if "vol" in df.columns else ("volume" if "volume" in df.columns else ("v" if "v" in df.columns else "Volume"))
    ts_col = "ts" if "ts" in df.columns else ("timestamp" if "timestamp" in df.columns else ("t" if "t" in df.columns else "time"))
    
    return [
        {"close": float(row[close_col]), "volume": float(row[vol_col]) if vol_col in df.columns else 1.0, "ts": int(row[ts_col]) if ts_col in df.columns else i}
        for i, (_, row) in enumerate(df.iterrows())
    ]


def load_funding_rates(symbol: str) -> list[float]:
    """Load historical funding rates."""
    base = symbol.replace("-USDT-SWAP", "").replace("-USDT", "")
    csv_path = REPO / "data" / f"{base}_funding_binance.csv"
    if not csv_path.exists():
        csv_path = REPO / "data" / f"{base}_funding.csv"
    if not csv_path.exists():
        csv_path = REPO / "data" / "carry" / f"{base}_funding.csv"
    if not csv_path.exists():
        return [0.0] * 1000
    df = pd.read_csv(csv_path)
    # Handle different column names
    fr_col = "fundingRate" if "fundingRate" in df.columns else ("funding_rate" if "funding_rate" in df.columns else ("fr" if "fr" in df.columns else "rate"))
    return df[fr_col].astype(float).tolist()


def generate_ensemble_signals(prices: list[float], price_data: list[dict], funding_rates: list[float]) -> list[Signal]:
    """Generate signals using the legacy ensemble approach."""
    signals_list = []
    window = 50  # minimum lookback
    
    for i in range(window, len(prices)):
        price_window = prices[i-window:i]
        pd_window = price_data[i-window:i]
        fr = funding_rates[i] if i < len(funding_rates) else 0.0
        
        signals = [
            mean_reversion_signal("BTC-USDT-SWAP", price_window, window=20, z_threshold=2.0, regime_window=50),
            funding_rate_signal("BTC-USDT-SWAP", fr, threshold=0.001),
        ]
        if len(pd_window) >= 20:
            signals.append(momentum_signal("BTC-USDT-SWAP", pd_window, short_window=5, long_window=20, regime_window=50))
        
        ensemble = ensemble_signal("BTC-USDT-SWAP", signals)
        signals_list.append(ensemble)
    
    return signals_list


def generate_consensus_signals(prices: list[float], price_data: list[dict], funding_rates: list[float], consensus_gate: ConsensusGate) -> list[Signal]:
    """Generate signals using the ConsensusGate approach."""
    signals_list = []
    traders = create_default_swarm()
    window = 50
    
    for i in range(window, len(prices)):
        price_window = prices[i-window:i]
        pd_window = price_data[i-window:i]
        fr = funding_rates[i] if i < len(funding_rates) else 0.0
        
        # Build context
        context = MarketContext(
            asset="BTC-USDT-SWAP",
            prices=price_window,
            price_data=pd_window,
            funding_rate=fr,
        )
        
        consensus = consensus_gate.compute_consensus(
            asset="BTC-USDT-SWAP",
            cohort="majors",
            traders=traders,
            context=context,
        )
        
        if consensus.consensus_reached:
            sig = Signal(
                strategy=f"consensus_{consensus.cohort}",
                asset="BTC-USDT-SWAP",
                direction=consensus.direction,
                confidence_bps=consensus.consensus_confidence_bps,
                entry_price=prices[i],
                rationale=consensus.rationale,
            )
        else:
            # No consensus = neutral
            sig = Signal(
                strategy=f"consensus_{consensus.cohort}",
                asset="BTC-USDT-SWAP",
                direction="NEUTRAL",
                confidence_bps=0,
                entry_price=prices[i],
                rationale="No consensus reached",
            )
        signals_list.append(sig)
    
    return signals_list


def run_backtest(prices: list[float], signals: list[Signal]) -> ValidationResult:
    """Run backtest and return metrics."""
    result = backtest_simple(
        prices=prices[len(prices)-len(signals):],  # Align prices with signals
        signals=signals,
        initial_capital=10000,
        fee_bps=5.0,
        slippage_bps=3.0,
        funding_cost_bps=0.0,
    )
    
    # Calculate Calmar
    cagr = (result.total_return_bps / 10000.0) / (len(signals) / (365 * 24))  # rough annualization
    calmar = cagr / (result.max_drawdown_bps / 10000.0) if result.max_drawdown_bps > 0 else 0
    
    return ValidationResult(
        name="",
        total_return_bps=result.total_return_bps,
        sharpe_ratio=result.sharpe_ratio,
        max_drawdown_bps=result.max_drawdown_bps,
        calmar_ratio=calmar,
        num_trades=result.num_trades,
        win_rate=result.win_rate,
    )


async def main():
    print("=" * 80)
    print("CONSENSUS VALIDATION — A/B Test: ConsensusGate vs Ensemble Baseline")
    print("=" * 80)
    
    symbols = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP"]
    
    all_results = {"ensemble": {}, "consensus": {}}
    
    for symbol in symbols:
        print(f"\n--- {symbol} ---")
        
        # Load data
        prices = load_price_data(symbol)
        price_data = load_price_data_dicts(symbol)
        funding_rates = load_funding_rates(symbol)
        
        if len(prices) < 100:
            print(f"  Insufficient data for {symbol}, skipping")
            continue
        
        print(f"  Loaded {len(prices)} price bars, {len(funding_rates)} funding rates")
        
        # Create consensus gate with default settings
        consensus_gate = create_default_consensus_gate()
        
        # --- Ensemble baseline ---
        print("  Running ensemble baseline...")
        ensemble_signals = generate_ensemble_signals(prices, price_data, funding_rates)
        ensemble_result = run_backtest(prices, ensemble_signals)
        ensemble_result.name = f"ensemble_{symbol}"
        all_results["ensemble"][symbol] = ensemble_result
        
        print(f"    Trades: {ensemble_result.num_trades}, Return: {ensemble_result.total_return_bps/100:.2f}%, "
              f"Sharpe: {ensemble_result.sharpe_ratio:.2f}, Calmar: {ensemble_result.calmar_ratio:.2f}, "
              f"MaxDD: {ensemble_result.max_drawdown_bps/100:.2f}%")
        
        # --- Consensus gate ---
        print("  Running consensus gate...")
        consensus_signals = generate_consensus_signals(prices, price_data, funding_rates, consensus_gate)
        consensus_result = run_backtest(prices, consensus_signals)
        consensus_result.name = f"consensus_{symbol}"
        
        # Calculate consensus stats
        consensus_reached = sum(1 for s in consensus_signals if s.direction != "NEUTRAL")
        consensus_result.consensus_reached_pct = consensus_reached / len(consensus_signals) * 100
        all_results["consensus"][symbol] = consensus_result
        
        print(f"    Trades: {consensus_result.num_trades}, Return: {consensus_result.total_return_bps/100:.2f}%, "
              f"Sharpe: {consensus_result.sharpe_ratio:.2f}, Calmar: {consensus_result.calmar_ratio:.2f}, "
              f"MaxDD: {consensus_result.max_drawdown_bps/100:.2f}%, "
              f"Consensus reached: {consensus_result.consensus_reached_pct:.1f}%")
        
        # --- Comparison ---
        print(f"\n  COMPARISON ({symbol}):")
        print(f"    Ensemble:    Sharpe={ensemble_result.sharpe_ratio:.2f}, Calmar={ensemble_result.calmar_ratio:.2f}, "
              f"Trades={ensemble_result.num_trades}, Return={ensemble_result.total_return_bps/100:.2f}%")
        print(f"    Consensus:   Sharpe={consensus_result.sharpe_ratio:.2f}, Calmar={consensus_result.calmar_ratio:.2f}, "
              f"Trades={consensus_result.num_trades}, Return={consensus_result.total_return_bps/100:.2f}%")
        
        sharpe_diff = consensus_result.sharpe_ratio - ensemble_result.sharpe_ratio
        calmar_diff = consensus_result.calmar_ratio - ensemble_result.calmar_ratio
        trade_diff = consensus_result.num_trades - ensemble_result.num_trades
        
        print(f"    Delta:       Sharpe={sharpe_diff:+.2f}, Calmar={calmar_diff:+.2f}, "
              f"Trades={trade_diff:+d}")
        
        if sharpe_diff > 0 and calmar_diff > 0:
            print("    [+] CONSENSUS IMPROVES both Sharpe and Calmar")
        elif sharpe_diff > 0:
            print("    [+] CONSENSUS IMPROVES Sharpe only")
        elif calmar_diff > 0:
            print("    [+] CONSENSUS IMPROVES Calmar only")
        else:
            print("    [-] CONSENSUS DOES NOT IMPROVE risk-adjusted returns")
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    for method in ["ensemble", "consensus"]:
        print(f"\n{method.upper()}:")
        for symbol, result in all_results[method].items():
            print(f"  {symbol}: Sharpe={result.sharpe_ratio:.2f}, Calmar={result.calmar_ratio:.2f}, "
                  f"Return={result.total_return_bps/100:.2f}%, Trades={result.num_trades}")
            if method == "consensus":
                print(f"    Consensus reached: {result.consensus_reached_pct:.1f}%")
    
    # Overall verdict
    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)
    
    for symbol in symbols:
        if symbol in all_results["ensemble"] and symbol in all_results["consensus"]:
            e = all_results["ensemble"][symbol]
            c = all_results["consensus"][symbol]
            
            sharpe_improved = c.sharpe_ratio > e.sharpe_ratio
            calmar_improved = c.calmar_ratio > e.calmar_ratio
            
            if sharpe_improved and calmar_improved:
                verdict = "[+] IMPROVES both Sharpe and Calmar"
            elif sharpe_improved:
                verdict = "[~] IMPROVES Sharpe only"
            elif calmar_improved:
                verdict = "[~] IMPROVES Calmar only"
            else:
                verdict = "[-] NO IMPROVEMENT in risk-adjusted returns"
            
            print(f"  {symbol}: {verdict}")
    
    # Save results
    output = {
        "ensemble": {k: v.__dict__ for k, v in all_results["ensemble"].items()},
        "consensus": {k: v.__dict__ for k, v in all_results["consensus"].items()},
    }
    
    output_path = REPO / "reports" / "consensus_validation.json"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\nResults saved to {output_path}")
    
    return 0


if __name__ == "__main__":
    asyncio.run(main())