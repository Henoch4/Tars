"""Run the frozen age-variant experiment in paper shadow mode."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.meme_age_runner import AgeVariantRunner
from src.meme_shadow_runner import append_rows, observe_once


def run_once(*, observations: str, decisions: str, limit: int = 25,
             runner: AgeVariantRunner | None = None) -> dict:
    runner = runner or AgeVariantRunner()
    seen = []
    exits = []

    def on_snapshot(snapshot, ts):
        was_open = (f"baseline:{snapshot.mint}" in runner.baseline.open or
                    f"variant:{snapshot.mint}" in runner.variant.open)
        result = runner.ingest(snapshot, observed_at=ts)
        seen.append(result.decision)
        if was_open and snapshot.price_usd and snapshot.liquidity_usd:
            exits.extend(runner.mark(snapshot.mint, price=snapshot.price_usd,
                                     liquidity_usd=snapshot.liquidity_usd, ts=ts))

    summary = observe_once(limit=limit, output=observations, on_snapshot=on_snapshot)
    append_rows(decisions, seen + exits + [{"event": "experiment_round", "summary": summary,
                                            "ledger": runner.summary(), "ts": time.time()}])
    return {"summary": summary, "ledger": runner.summary()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="age_variant_v1 paper runner")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--observations", default="data/meme_satellite/shadow_observations.jsonl")
    parser.add_argument("--decisions", default="data/meme_satellite/age_variant_v1_decisions.jsonl")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args(argv)
    runner = AgeVariantRunner()
    while True:
        print(json.dumps(run_once(observations=args.observations,
                                  decisions=args.decisions, limit=args.limit,
                                  runner=runner), sort_keys=True), flush=True)
        if not args.loop:
            return 0
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
