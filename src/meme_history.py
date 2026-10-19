"""Deployer-history bootstrap (self-supplied creator history).

No free source tracks prior rugs per deployer, so we compound our own:
every screen record now carries creator_address + rugcheck_rugged, and this
module aggregates per-deployer launch counts and rugged flags from the
blotter. Starts empty, unbiased toward our screened population (documented
limitation: we only learn about deployers we screen).

Cold-start policy (explicit): unknown deployer reads as rugs=0 (clean).
Rationale: unknown is not known-bad, and blocking on empty history would
re-block the funnel this module exists to open. The assumption weakens
automatically as data compounds (serial operators surface by volume fast).

FEEDBACK-LOOP WARNING (read before wiring): rugged_seen comes from
RugCheck determinations (external), but the SAMPLE is our own screens.
Our selection shapes whose history we learn. Never treat these counts as
population statistics — they are prior-observation counts, nothing more.

Gate wiring (2026-10, deliberate): enrich reads rugged_seen for the mint's
creator (excluding the mint itself) into creator_past_rugs. Serial-launch
volume stays informational (diagnose table) until measured data justifies
a count gate.
"""
from __future__ import annotations

import json
import os
import time


def deployer_stats(blotter_path: str) -> dict:
    """{creator: {launches, rugged_seen, first_ts, last_ts}} over screen rows
    carrying creator_address. Rows without it (pre-change history) are
    skipped, never guessed."""
    stats: dict[str, dict] = {}
    try:
        with open(blotter_path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return stats
    seen_mints: dict[str, set] = {}
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("event") != "screen":
            continue
        creator = r.get("creator_address")
        mint = r.get("mint")
        if not creator or not mint:
            continue
        entry = stats.setdefault(creator, {
            "launches": 0, "rugged_seen": 0,
            "first_ts": None, "last_ts": None})
        mints = seen_mints.setdefault(creator, set())
        ts = r.get("ts")
        if entry["first_ts"] is None or (isinstance(ts, (int, float))
                                         and ts < entry["first_ts"]):
            entry["first_ts"] = ts
        if isinstance(ts, (int, float)) and (
                entry["last_ts"] is None or ts > entry["last_ts"]):
            entry["last_ts"] = ts
        if mint not in mints:
            mints.add(mint)
            entry["launches"] += 1
            if r.get("rugcheck_rugged") is True:
                entry["rugged_seen"] += 1
    return stats


def serial_deployers(blotter_path: str, min_launches: int = 3) -> list:
    """Creators at/above the launch count, most prolific first."""
    stats = deployer_stats(blotter_path)
    out = [(c, s["launches"], s["rugged_seen"])
           for c, s in stats.items() if s["launches"] >= min_launches]
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


_HISTORY_CACHE: dict = {"path": None, "mtime": 0.0, "data": {}}


def rebuild_history(blotter_path: str, out_path: str) -> dict:
    """Aggregate blotter -> history file (atomic write). Returns stats."""
    stats = deployer_stats(blotter_path)
    payload = {"built_ts": time.time(), "deployer_count": len(stats),
               "deployers": stats}
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, out_path)
    global _HISTORY_CACHE
    try:
        _HISTORY_CACHE = {"path": out_path,
                          "mtime": os.path.getmtime(out_path), "data": payload}
    except OSError:
        pass
    return stats


def load_history(out_path: str) -> dict:
    """Cached read of the history file (mtime-checked; missing -> {}).
    Cheap enough to call per enrichment."""
    global _HISTORY_CACHE
    try:
        mtime = os.path.getmtime(out_path)
    except OSError:
        return {}
    if _HISTORY_CACHE.get("path") == out_path and \
            _HISTORY_CACHE.get("mtime") == mtime:
        return _HISTORY_CACHE["data"]
    try:
        with open(out_path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}
    _HISTORY_CACHE = {"path": out_path, "mtime": mtime, "data": payload}
    return payload


def rugs_for_creator(history: dict, creator: str | None) -> int:
    """Prior rugged-flagged launches for a creator. Unknown (or no creator)
    reads as 0 downstream (cold-start policy, see module docstring).
    Caveat: a mint re-screened across cycles counts twice (bounded: each
    re-screen adds at most one launch and re-affirms its own rugged flag,
    same direction, never a flip)."""
    if not creator:
        return 0
    rec = (history.get("deployers") or {}).get(creator)
    if not rec:
        return 0
    return int(rec.get("rugged_seen", 0))
