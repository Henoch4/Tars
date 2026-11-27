"""GTA VI shadow-mode discovery loop.

Runs the read-only GTA scanner and appends candidate decisions to JSONL. This
script has no wallet imports, no signing path, and no broadcast capability.
It is suitable for a disabled-by-default systemd unit once deployed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.gta_discovery import append_observations, discover_and_scan  # noqa: E402
from src.gta_event import GtaEventPolicy  # noqa: E402
from src.meme_discovery import DiscoveryError  # noqa: E402


DEFAULT_CONFIG = ROOT / "config" / "gta_event.yaml"
DEFAULT_LOG = ROOT / "data" / "gta_event" / "shadow_observations.jsonl"


def _timestamp(value):
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed


def load_policy(path: str | Path = DEFAULT_CONFIG) -> GtaEventPolicy:
    """Load the event policy; malformed config fails closed."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("GTA config must be a mapping")
    exits = raw.get("exits") or {}
    ladder = tuple(
        (float(item["sell_pct"]), float(item["gain_pct"]))
        for item in (exits.get("take_profit_ladder") or ())
        if isinstance(item, dict) and "sell_pct" in item and "gain_pct" in item
    )
    return GtaEventPolicy(
        enabled=bool(raw.get("enabled", False)),
        live_enabled=bool(raw.get("live_enabled", False)),
        start_utc=_timestamp(raw.get("start_utc")),
        end_utc=_timestamp(raw.get("end_utc")),
        max_total_usd=float(raw.get("max_total_usd", 0.0)),
        max_per_token_usd=float(raw.get("max_per_token_usd", 0.0)),
        max_open_positions=int(raw.get("max_open_positions", 0)),
        max_top10_pct=float(raw.get("max_top10_pct", 20.0)),
        min_liquidity_multiple=float(raw.get("min_liquidity_multiple", 10.0)),
        min_holders=int(raw.get("min_holders", 100)),
        keywords=tuple(str(k) for k in (raw.get("keywords") or ())),
        stop_loss_pct=float(exits.get("stop_loss_pct", 35.0)),
        take_profit_ladder=ladder or ((45.0, 100.0), (30.0, 200.0)),
        runner_trailing_stop_pct=float(exits.get("runner_trailing_stop_pct", 25.0)),
        max_hold_hours=float(exits.get("max_hold_hours", 96.0)),
        force_flat_utc=_timestamp(exits.get("force_flat_utc")),
    )


def run_once(policy: GtaEventPolicy, *, limit: int, output: str | Path,
             bundle_budget: int = 0) -> dict:
    started = time.time()
    try:
        observations = discover_and_scan(
            policy, limit=limit, bundle_budget=bundle_budget)
        append_observations(output, observations)
        return {
            "event": "gta_shadow_round",
            "ok": True,
            "observations": len(observations),
            "allowed": sum(1 for row in observations if row.get("allowed")),
            "elapsed_s": round(time.time() - started, 3),
            "policy_enabled": policy.enabled,
        }
    except DiscoveryError as exc:
        error = {
            "event": "gta_shadow_error",
            "ok": False,
            "error": type(exc).__name__,
            "reason": str(exc),
            "ts": time.time(),
        }
        append_observations(output, [error])
        return error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TARS GTA shadow scanner (read-only)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default=str(DEFAULT_LOG))
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument(
        "--bundle-budget", type=int, default=0,
        help="maximum candidates per round to fund-graph (default: 0)",
    )
    args = parser.parse_args(argv)

    policy = load_policy(args.config)
    if args.loop:
        print(f"GTA shadow scanner running (interval={args.interval}s, limit={args.limit})")
        print(f"policy_enabled={policy.enabled}; output={args.output}")
        while True:
            print(json.dumps(run_once(
                policy, limit=args.limit, output=args.output,
                bundle_budget=args.bundle_budget), sort_keys=True), flush=True)
            time.sleep(args.interval)
    else:
        print(json.dumps(run_once(
            policy, limit=args.limit, output=args.output,
            bundle_budget=args.bundle_budget), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
