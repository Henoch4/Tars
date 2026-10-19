"""Survivor observer v1 — watch graduated tokens past the loop's exits.

The paper loop exits losers fast; calibration needs the opposite: tokens
that SURVIVE, observed without interfering. This script never trades,
never writes loop state (positions/blotter/risk), only appends to
data/meme_satellite/survivors/.

Two modes (manual runs only — no service, no timer yet):
    meme_survivor_watch.py --enroll    # enroll newly-seen graduates (cap 5/week)
    meme_survivor_watch.py --snapshot  # append one observation row per enrollee

Enrollment source: blotter screen events whose reasons contain the survival
confirmation note. Storage: survivors/index.json (enrolled set) + per-mint
JSONL observation rows. Future move to artifacts/survivors/ comes with the
Ask-3 migration; until then this lives beside blotter/positions.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SURVIVAL_NOTE = "survival confirmed"
MAX_ENROLL_PER_WEEK = 5
WEEK_S = 7 * 24 * 3600


def _survivors_dir():
    from src.meme_loop import BLOTTER_PATH
    base = os.path.join(os.path.dirname(os.path.abspath(BLOTTER_PATH)),
                        "survivors")
    os.makedirs(base, exist_ok=True)
    return base


def _read_blotter():
    from src.meme_loop import BLOTTER_PATH
    try:
        with open(BLOTTER_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _index(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"enrolled": {}}


def _save_index(path, index):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f)
    os.replace(tmp, path)


def enroll(now=None):
    """Enroll graduated mints seen in the blotter, capped per week."""
    now = time.time() if now is None else now
    base = _survivors_dir()
    index = _index(os.path.join(base, "index.json"))
    enrolled = index.setdefault("enrolled", {})
    recent = sum(1 for e in enrolled.values()
                 if now - float(e.get("enrolled_ts", 0)) < WEEK_S)
    added = []
    for r in _read_blotter():
        if r.get("event") != "screen":
            continue
        mint = r.get("mint")
        if not mint or mint in enrolled:
            continue
        notes = " ".join(str(x) for x in (r.get("reasons") or []))
        if SURVIVAL_NOTE not in notes:
            continue
        if recent >= MAX_ENROLL_PER_WEEK:
            break
        enrolled[mint] = {"enrolled_ts": now,
                          "via_cycle": r.get("cycle"),
                          "note": "survival confirmed in screen verdict"}
        added.append(mint)
        recent += 1
    _save_index(os.path.join(base, "index.json"), index)
    print(f"enrolled {len(added)}" + (f": {', '.join(added)}" if added else ""))
    return added


def snapshot(now=None, enrich=None):
    """Append one observation row per enrollee (price/liq/volume/venue)."""
    from src.meme_discovery import DiscoveryError, enrich_mint
    now = time.time() if now is None else now
    base = _survivors_dir()
    index = _index(os.path.join(base, "index.json"))
    n_ok = n_err = 0
    for mint in sorted(index.get("enrolled", {})):
        row = {"mint": mint, "ts": now}
        try:
            info = (enrich or enrich_mint)(mint)
            row.update({
                "price_usd": info.get("price_usd"),
                "liquidity_usd": info.get("liquidity_usd"),
                "volume_24h_usd": info.get("volume_24h_usd"),
                "venue": info.get("venue"),
                "graduated": info.get("graduated"),
            })
            n_ok += 1
        except DiscoveryError as e:
            row["error"] = str(e)[:200]
            n_err += 1
        except Exception as e:  # never crash the watch on one mint
            row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            n_err += 1
        with open(os.path.join(base, f"{mint}.jsonl"), "a",
                   encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    print(f"snapshot: {n_ok} ok, {n_err} errors "
          f"({len(index.get('enrolled', {}))} enrolled)")
    return n_ok, n_err


def main(argv=None):
    ap = argparse.ArgumentParser(description="Survivor observer (read-only watch)")
    ap.add_argument("--enroll", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args(argv)
    if args.enroll:
        enroll()
    elif args.snapshot:
        snapshot()
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
