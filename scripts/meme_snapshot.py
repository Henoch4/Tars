#!/usr/bin/env python3
"""Paper-loop demo: snapshot one mint through the feed + scream filter.

Usage (on the VPS, no pasting needed):
    cd /opt/tars && ./venv/bin/python scripts/meme_snapshot.py [MINT]

Credentials load from /etc/tars/agent.env when present (HELIUS_*), else
from the process environment. MINT defaults to wrapped SOL. Read-only:
zero transactions, only RPC reads.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WSOL = "So11111111111111111111111111111111111111112"
# Demo mints (huge holder counts like wSOL trip Helius' account-scan
# limit — real memecoins don't). `demo` snapshots both, no args = wSOL.
DEMO_MINTS = [
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
]
AGENT_ENV = "/etc/tars/agent.env"


def _load_agent_env(path=AGENT_ENV):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if (key.startswith("HELIUS_") or key.startswith("SOLAMI_")) and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main(argv):
    _load_agent_env()
    from src.meme_feed import FeedError, HeliusFeed
    from src.meme_satellite import scream_filter

    arg = argv[1] if len(argv) > 1 else WSOL
    if arg == "discover":
        return _discover_round()
    if arg == "blur":
        rest = argv[2:]
        probe = "probe" in rest
        dur = next((float(x) for x in rest if x != "probe"), 45.0)
        return _blur_round(dur, probe=probe)
    mints = DEMO_MINTS if arg == "demo" else [arg]
    feed = HeliusFeed()
    for mint in mints:
        print("=" * 60)
        try:
            snap = feed.snapshot_for_mint(
                mint, {"age_s": 1e9, "graduated": True})
        except FeedError as e:
            print("MINT:", mint)
            print("SKIPPED (feed):", e)
            continue
        print("SNAPSHOT:", snap)
        verdict = scream_filter(snap)
        print("FILTER:", "PASS" if verdict.passed else f"SKIP ({verdict.rejected_by})")
        for note in verdict.reasons:
            print("  -", note)
    return 0


def _discover_round(limit: int = 5):
    """Full paper loop, one round: DexScreener discovery -> enrichment ->
    Helius snapshot -> scream verdict. No keys beyond Helius, no trades."""
    from src.meme_discovery import DiscoveryError, discover_candidates, enrich_mint
    from src.meme_feed import FeedError, HeliusFeed
    from src.meme_satellite import scream_filter

    try:
        candidates = discover_candidates(limit=limit)
    except DiscoveryError as e:
        print("DISCOVERY SKIPPED:", e)
        return 0
    print(f"candidates: {len(candidates)} fresh solana mints")
    feed = HeliusFeed()
    for cand in candidates:
        mint = cand["mint"]
        print("=" * 60)
        print("MINT:", mint)
        try:
            info = enrich_mint(mint)
        except DiscoveryError as e:
            print("SKIPPED (enrich):", e)
            continue
        extra = {
            "age_s": info["age_s"],
            "liquidity_usd": info["liquidity_usd"],
            "graduated": info["graduated"],
        }
        try:
            snap = feed.snapshot_for_mint(mint, extra)
        except FeedError as e:
            print("SKIPPED (feed):", e)
            continue
        verdict = scream_filter(snap)
        print("FILTER:", "PASS" if verdict.passed else f"SKIP ({verdict.rejected_by})")
        for note in verdict.reasons:
            print("  -", note)
    return 0


def _blur_round(duration_s: float = 45.0, probe: bool = False):
    """Live Blur verification: open the firehose, collect, report counts +
    sample mints. Needs SOLAMI_API_KEY with the DataApi role (env or
    /etc/tars/agent.env). Any failure prints a typed reason, never fake data.
    probe=True additionally prints the first-seen key shape per interesting
    message type (diagnoses list/event shapes that parse to zero)."""
    import asyncio
    import os
    from src.solami_blur import BlurError, collect_blur_candidates

    _load_agent_env()  # no-op unless /etc/tars/agent.env exists
    key = (os.getenv("SOLAMI_API_KEY") or "").strip()
    shapes = {}

    def _on_raw(msg):
        if not probe or not isinstance(msg, dict):
            return
        t = str(msg.get("type"))
        if t in shapes or t not in ("launches", "graduating", "graduated",
                                    "graduation", "pool_create",
                                    "token_create"):
            return
        keys = sorted(msg.keys())
        item_keys = None
        items = msg.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            item_keys = sorted(items[0].keys())
        shapes[t] = (keys, item_keys)

    try:
        store = asyncio.run(collect_blur_candidates(
            key, duration_s=duration_s,
            on_raw=_on_raw if probe else None))
    except BlurError as e:
        print("BLUR SKIPPED:", e)
        return 0
    print(f"blur: {len(store['mints'])} candidate mints, "
          f"{store['skipped']} skipped")
    print("event counts:", store["counts"])
    if store.get("unparsed"):
        print("unparsed WANT types (counted but yielded zero):",
              store["unparsed"])
    for cand in store["mints"][:10]:
        print("  -", cand["mint"], f"(via {cand['via']})")
    if probe:
        for t in ("token_create", "pool_create", "graduation", "launches",
                  "graduating", "graduated"):
            if t in shapes:
                keys, item_keys = shapes[t]
                print(f"shape {t}: keys={keys}"
                      + (f" items[0]={item_keys}" if item_keys else ""))
            else:
                print(f"shape {t}: NOT SEEN")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
