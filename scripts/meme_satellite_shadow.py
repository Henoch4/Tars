"""Run the composed main-satellite runtime in read-only shadow mode."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.meme_satellite_runtime import build_single_position_supervisor
from src.meme_shadow_runner import observe_once
from src.meme_supervisor import SupervisorConfig


def build_shadow(output: str):
    # Shadow mode intentionally has no position, balance reader, owner, or
    # sell handler. The observer still performs real market-data screening.
    return build_single_position_supervisor(
        SupervisorConfig(enabled=True, live_enabled=False,
                         state_path="data/meme_satellite/supervisor_state.json",
                         event_log_path="data/meme_satellite/supervisor_events.jsonl"),
        screen_fn=lambda: (observe_once(output=output), [])[1],
        position_loader=lambda: None,
        position_saver=lambda _position: None,
        balance_reader=None,
        owner="",
        sell_fn=lambda **_: {"broadcast": False, "reason": "shadow mode"},
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Main memecoin satellite shadow runner")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--output", default="data/meme_satellite/shadow_observations.jsonl")
    args = parser.parse_args(argv)
    supervisor = build_shadow(args.output)
    while True:
        print(supervisor.run_once(), flush=True)
        if not args.loop:
            return 0
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
