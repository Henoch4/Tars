"""Satellite runner — ties the loop to live data feeds, runs continuously.

This is the G1 paper bot: discovers via Solami Blur (or DexScreener fallback),
enriches via Helius, screens via the scream filter, enters paper positions,
monitors exits, records PnL. Zero funds moved — paper-only until G1 passes.

Usage on the VPS:
    ./venv/bin/python scripts/meme_runner.py              # one round
    ./venv/bin/python scripts/meme_runner.py --loop       # continuous
    ./venv/bin/python scripts/meme_runner.py --loop --interval 120
    ./venv/bin/python scripts/meme_runner.py --status     # portfolio snapshot

Reads credentials from /etc/tars/agent.env (HELIUS_*, SOLAMI_*).
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AGENT_ENV = "/etc/tars/agent.env"


def _load_env():
    """Load HELIUS_* and SOLAMI_* from agent.env into os.environ."""
    for path in (AGENT_ENV,):
        try:
            for raw in open(path, encoding="utf-8", errors="replace"):
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if (key.startswith("HELIUS_") or key.startswith("SOLAMI_")) \
                        and key not in os.environ:
                    os.environ[key] = val.strip().strip('"').strip("'")
        except OSError:
            pass


def _discover(limit=10):
    """Discovery: Solami Blur preferred, DexScreener fallback.

    Maturity bias: graduated/graduating tokens first — DexScreener hasn't
    indexed brand-new launches yet (they SKIP as 'too fresh'), while
    post-curve tokens enrich cleanly. Fresh launches still flow when
    nothing mature is available."""
    # Lower = earlier in the queue.
    _MATURITY = {"graduated": 0, "graduating": 1, "graduation": 2,
                 "pool_create": 3, "token_create": 4, "launches": 5}
    _load_env()
    key = (os.environ.get("SOLAMI_API_KEY") or "").strip()
    if key:
        try:
            from src.solami_blur import collect_blur_candidates
            store = asyncio.run(collect_blur_candidates(key, duration_s=20))
            raw = store.get("mints", []) or []
            # Defensive: only dicts with non-empty string mints survive,
            # regardless of upstream shape drift.
            cands = []
            for c in raw:
                if not isinstance(c, dict):
                    continue
                mint = c.get("mint")
                if isinstance(mint, str) and mint.strip():
                    cands.append({"mint": mint.strip(),
                                  "via": c.get("via", "?")})
            cands.sort(key=lambda c: (_MATURITY.get(c.get("via"), 9),
                                      c["mint"]))
            if cands:
                creates = store.get('counts', {}).get('token_create', 0)
                grads = (store.get('counts', {}).get('graduated', 0)
                         + store.get('counts', {}).get('graduating', 0))
                return ([{"mint": c["mint"], "via": c.get("via", "?")}
                         for c in cands[:limit]],
                        f"blur({creates} fresh, {grads} graduating/graduated)")
        except Exception as e:
            print(f"  blur skipped: {e}")
    # DexScreener fallback (keyless)
    from src.meme_discovery import discover_candidates
    cands = discover_candidates(limit=limit)
    return ([{"mint": c["mint"], "via": "dexscreener"} for c in cands],
            "dexscreener")


def _history_path():
    """deployer_history.json beside the blotter (same durable dir)."""
    from src.meme_loop import BLOTTER_PATH
    return os.path.join(os.path.dirname(os.path.abspath(BLOTTER_PATH)),
                        "deployer_history.json")


def _forensics_cache_path(mint: str) -> str:
    from src.meme_loop import BLOTTER_PATH
    base = os.path.join(os.path.dirname(os.path.abspath(BLOTTER_PATH)),
                        "forensics")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    safe = "".join(c for c in mint if c.isalnum())[:64] or "unknown"
    return os.path.join(base, f"{safe}.json")


def _cached_forensics(mint: str, creator=None) -> dict:
    """Immutable creation history: disk cache hit avoids ~11 RPC calls.
    Misses fetch live once, then persist. Failures raise (caller soft)."""
    import json as _json
    from src.meme_launch import LaunchForensics
    path = _forensics_cache_path(mint)
    try:
        with open(path, encoding="utf-8") as f:
            cached = _json.load(f)
        if isinstance(cached, dict) and cached.get("mint") == mint:
            # Refetch only to fill a missing dev verdict once the creator
            # is known; block-0 counts are immutable and stay cached.
            if cached.get("dev_sold_minute1") is None and creator:
                raise KeyError("dev verdict pending")
            return cached
    except (OSError, ValueError, KeyError):
        pass
    result = LaunchForensics().examine(mint, creator=creator)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(result, f)
        os.replace(tmp, path)
    except OSError:
        pass
    return result


def _enrich(mint, via=None):
    """Enrich one mint: Helius (chain truth, always works) + DexScreener
    (indexed data, may lag fresh mints) -> TokenSnapshot with price.
    Fresh tokens get partial snapshots (filter SKIP-defaults the gaps);
    only total Helius failure skips the mint."""
    from src.meme_discovery import DiscoveryError, enrich_mint
    from src.meme_feed import FeedError, HeliusFeed
    # DexScreener first (liquidity, volume, age, venue, price) — optional
    info = {}
    try:
        info = enrich_mint(mint)
    except DiscoveryError as e:
        info = {"_dex_error": str(e)}
    # Helius always (holders, authorities — chain truth, no indexing lag)
    extra = {
        "age_s": info.get("age_s"),
        "liquidity_usd": info.get("liquidity_usd"),
        "graduated": info.get("graduated", False),
        "mcap_usd": info.get("mcap_usd"),
        "pool_address": info.get("pool_address"),
        "symbol": info.get("symbol"),
    }
    # RugCheck (creator + rugged verdict — free, keyless). Fail-soft: any
    # failure leaves the keys absent and the filter SKIP-defaults them,
    # exactly as before this source existed. One GET per mint per cycle.
    try:
        from src.meme_rugcheck import rugcheck_mint
        rc = rugcheck_mint(mint)
        extra["creator_address"] = rc.get("creator_address")
        extra["rugcheck_rugged"] = rc.get("rugcheck_rugged")
        extra["rugcheck_score"] = rc.get("rugcheck_score")
        if rc.get("creator_hold_pct") is not None:
            extra["creator_hold_pct"] = rc.get("creator_hold_pct")
    except Exception:
        pass
    # Jupiter token profile (keyless). PRIMARY creator source now that
    # RugCheck's domain is dead (NXDOMAIN since 2026-10-18 23:25Z). The
    # `dev` field is the deployer wallet -> creator_address (which also
    # drives the on-chain creator_hold_pct = dev balance / supply computed
    # in HeliusFeed.snapshot_for_mint), and `dev_mints` is the serial-
    # deployer launch count (note only). GRADUATES ONLY: creator_address is
    # only consumed at Gate 2/3b (post-graduation), and /tokens/v2/search
    # throttles hard (~5 then 429 — measured 2026-10-19), so calling it for
    # newborns would starve the graduates that actually need it. Absent
    # values stay SKIP-default.
    try:
        if info.get("graduated"):
            from src.meme_jupiter import token_info
            prof = token_info(mint)
            if prof:
                if not extra.get("creator_address") and prof.get("dev"):
                    extra["creator_address"] = prof["dev"]
                if prof.get("dev_mints") is not None:
                    extra["creator_launches"] = prof["dev_mints"]
                if extra.get("creator_hold_pct") is None \
                        and prof.get("dev_hold_pct") is not None:
                    extra["creator_hold_pct"] = prof["dev_hold_pct"]
                if prof.get("organic_score") is not None:
                    extra["organic_score"] = prof["organic_score"]
    except Exception:
        pass
    # Launch forensics (block-0 buyers + dev-minute-1 — measurement only,
    # no gate reads these yet). Graduates ONLY: newborns die at unconfirmed
    # regardless, so spending ~11 RPC calls on them is pure load. Results
    # are immutable (creation history never changes) -> disk-cached per
    # mint forever. Fail-soft like everything above.
    try:
        if info.get("graduated"):
            from src.meme_launch import LaunchForensics
            forensic = _cached_forensics(mint,
                                         creator=extra.get("creator_address"))
            extra["block0_buyers"] = forensic.get("block0_buyers")
            extra["dev_sold_minute1"] = forensic.get("dev_sold_minute1")
    except Exception:
        pass
    # Deployer history (bootstrap): RugCheck verdicts (when alive) compounded
    # per creator unlock Gate 2. Unknown creators stay absent (missing
    # rejects); known-clean reads as rugs=0 per the cold-start policy.
    # Fail-soft like everything above.
    try:
        from src.meme_history import load_history, rugs_for_creator
        hist = load_history(_history_path())
        if extra.get("creator_address"):
            extra["creator_past_rugs"] = rugs_for_creator(
                hist, extra["creator_address"])
    except Exception:
        pass
    # Jupiter round-trip (Gate 4 measurement): graduates ONLY — newborns
    # die at Gate 1 before sellability matters. Quotes the real route at
    # entry size both ways; a honeypot surfaces as a failed/total-loss
    # sell leg (cost = 100%). Fail-soft: absent = filter rejects as
    # missing_data, never default-passes. ~3 keyless calls per graduate.
    try:
        if info.get("graduated"):
            from src.meme_jupiter import round_trip_cost_pct
            rt = round_trip_cost_pct(mint, price_usd=info.get("price_usd"))
            if rt is not None:
                extra["round_trip_cost_pct"] = rt
    except Exception:
        pass
    try:
        feed = HeliusFeed()
        snap = feed.snapshot_for_mint(mint, extra)
        snap._price_usd = info.get("price_usd") or 0
        snap._volume_24h_usd = info.get("volume_24h_usd") or 0
        # Jupiter price fallback when DexScreener has no price yet
        if not snap._price_usd:
            try:
                from src.meme_execute import token_prices
                px = token_prices([mint]).get(mint)
                if px:
                    snap._price_usd = px
            except Exception:
                pass
        return snap, None
    except FeedError as e:
        return None, f"feed: {e}"


def _price_updates(loop):
    """Fetch current prices for all open positions."""
    from src.meme_discovery import DiscoveryError, enrich_mint
    updates = {}
    for mint in list(loop.open_positions.keys()):
        try:
            info = enrich_mint(mint)
            if info.get("price_usd"):
                updates[mint] = {
                    "price": info["price_usd"],
                    "volume_24h_usd": info.get("volume_24h_usd"),
                    "lp_reserve_usd": info.get("liquidity_usd"),
                }
        except DiscoveryError:
            pass
    return updates


def _report_signal_mix(candidates, snapshots, snap_vias, limit):
    """One parseable line: via-tag mix (what Blur claimed) vs graduated-flag
    mix (what the filter read). Uses getattr so stub snapshots in tests
    (object()) land in 'unknown' instead of crashing."""
    import collections
    vias = collections.Counter(
        (c.get("via", "?") if isinstance(c, dict) else "?")
        for c in candidates[:limit])
    flags = collections.Counter()
    for via, snap in zip(snap_vias, snapshots):
        g = getattr(snap, "graduated", None)
        flags[(via, bool(g) if isinstance(g, bool) else "?")] += 1
    via_s = ",".join(f"{k}={v}" for k, v in sorted(vias.items()))
    flag_s = ",".join(f"{v}:{1 if g is True else 0 if g is False else '?'}={n}"
                      for (v, g), n in sorted(flags.items(),
                                              key=lambda kv: str(kv[0])))
    print(f"  signals: via[{via_s}] flag[{flag_s}]")


def run_one_round(loop, candidates=None):
    """One full cycle: discover → enrich → screen → enter → exit → report."""
    t0 = time.time()
    if candidates is None:
        mints, source = _discover(loop.max_candidates)
        # _discover returns [{"mint":..., "via":...}] dicts — pass through,
        # wrapping only bare strings (direct-call path).
        candidates = [c if isinstance(c, dict) else {"mint": c, "via": "?"}
                      for c in mints]
        label = source
    else:
        label = "direct"
    print(f"\n=== cycle {loop.cycle_count + 1} | {label} | {len(candidates)} candidates ===")

    # Deployer-history refresh (once per cycle, not per mint): compound
    # the blotter into deployer_history.json so enrich reads current
    # counts. Fail-soft: a stale/missing file just reads as {} (cold).
    try:
        from src.meme_history import rebuild_history
        from src.meme_loop import BLOTTER_PATH
        rebuild_history(BLOTTER_PATH, _history_path())
    except Exception:
        pass

    snapshots = []
    snap_vias = []  # parallel to snapshots: Blur via-tag per enriched mint
    for cand in candidates[:loop.max_candidates]:
        mint = cand.get("mint") if isinstance(cand, dict) else cand
        via = cand.get("via", "?") if isinstance(cand, dict) else "?"
        if not mint or not isinstance(mint, str):
            print(f"  skip [malformed candidate]")
            continue
        snap, err = _enrich(mint, via=via)
        if snap:
            snapshots.append(snap)
            snap_vias.append(via)
        else:
            print(f"  skip {mint[:12]}... [{via}]: {err}")

    # Instrumentation (no behavior): Blur's via-tag mix vs the graduated
    # flag the filter actually reads. A persistent gap (via says graduated,
    # flag says newborn) locates the leak: uncollected, unindexed, or
    # venue-set mismatch — each has a different fix.
    _report_signal_mix(candidates, snapshots, snap_vias,
                       loop.max_candidates)

    # Check exits on existing positions first
    price_updates = _price_updates(loop)
    result = loop.run_cycle(snapshots, price_updates)

    # Report
    p = result["portfolio"]
    print(f"  screened: {result['screened']} | passed: {result['passed']} "
          f"| entered: {result['entered']} | exited: {result['exited']}")
    if result["exit_details"]:
        for e in result["exit_details"]:
            print(f"  EXIT {e['mint'][:12]}...: {e['reason']} "
                  f"net=${e['net_usd']:.4f}")
    print(f"  portfolio: {p['open_positions']} open | "
          f"${p['spent_usd']:.2f} spent | "
          f"unrealized ${p['unrealized_usd']:.4f} | "
          f"realized ${p['realized_net_usd']:.4f}")
    print(f"  ({time.time() - t0:.1f}s)")
    return result


def show_status(loop):
    p = loop.portfolio()
    print(f"=== TARS Satellite | cycle {p['cycle_count']} ===")
    print(f"halt: {'ENGAGED (entries blocked)' if loop.halted() else 'off'}")
    print(f"open positions: {p['open_positions']}")
    print(f"session spend:  ${p['spent_usd']:.2f} / ${p['max_session_usd']:.2f}")
    print(f"unrealized:     ${p['unrealized_usd']:.4f}")
    print(f"realized net:   ${p['realized_net_usd']:.4f}")
    print(f"total net:      ${p['total_net_usd']:.4f}")
    for m, d in p["positions"].items():
        print(f"  {d['symbol']:12s} entry={d['entry']:.6f} last={d['last']:.6f} "
              f"peak={d['peak']:.6f} pnl=${d['unrealized']:.4f} "
              f"hold={d['hold_h']}h")


def show_diagnose():
    """Histogram of screen verdicts from the blotter: WHY candidates fail.
    Reads the append-only blotter (same file the loop writes); read-only,
    never touches loop state. The rejected_by breakdown is what tells a
    too-tight filter (all missing_data/unconfirmed) apart from a market
    with genuinely nothing worth buying (risk hits: concentration, taxes).
    """
    import collections
    import json as _json
    from src.meme_loop import BLOTTER_PATH
    counts: dict = collections.Counter()
    n_screen = n_pass = 0
    try:
        with open(BLOTTER_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        print(f"no blotter yet ({BLOTTER_PATH}): {e}")
        return 0
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = _json.loads(ln)
        except ValueError:
            continue
        if r.get("event") != "screen":
            continue
        n_screen += 1
        if r.get("passed"):
            n_pass += 1
            counts["PASSED"] += 1
        else:
            counts[r.get("rejected_by") or "unknown"] += 1
    print(f"=== screen diagnosis | {n_screen} screened, {n_pass} passed "
          f"({n_pass / n_screen:.1%} pass rate) ===" if n_screen else
          "=== screen diagnosis | no screen events yet ===")
    for reason, n in counts.most_common():
        print(f"  {n:5d}  {reason}")
    _show_missing_fields(lines)
    _show_deployers()
    # concentration distribution: answers "bar too tight?" (near-misses
    # just over 50%) vs "market really is concentrated" (far over).
    _show_concentration_dist(lines)
    return 0


def _show_deployers():
    """Serial-deployer table from bootstrapped history: creators with 3+
    screened launches, most prolific first. Starts empty (history compounds
    from here); a future step may feed this into the filter — deliberately
    not wired yet (feedback-loop risk documented in meme_history)."""
    from src.meme_loop import BLOTTER_PATH
    from src.meme_history import serial_deployers
    rows = serial_deployers(BLOTTER_PATH, min_launches=3)[:10]
    if not rows:
        return
    print("--- serial deployers (bootstrapped history) ---")
    for creator, launches, rugged in rows:
        print(f"  {launches:4d} launches {rugged:3d} rugged  {creator[:12]}...")


def _show_missing_fields(lines):
    """For missing_data verdicts: WHICH field was fatal. Notes append in
    audit order, so the LAST 'X unobserved' note is the rejecting gate —
    not the first (Gate 0's rugcheck note precedes everything and would
    misattribute every row where the fetch merely failed). Tells a
    data-supply gap (one field always missing = no source feeds it) apart
    from scattered gaps. The supply gap names the next feed to build."""
    import re as _re
    import json as _json
    import collections as _collections
    fields = _collections.Counter()
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = _json.loads(ln)
        except ValueError:
            continue
        if r.get("event") != "screen" or r.get("rejected_by") != "missing_data":
            continue
        last = None
        for note in r.get("reasons") or []:
            m = _re.search(r"(\w+) unobserved", str(note))
            if m:
                last = m.group(1)
        if last:
            fields[last] += 1
    if not fields:
        return
    print("--- missing_data by field (data-supply gaps) ---")
    for field, n in fields.most_common():
        print(f"  {n:5d}  {field}")


def _show_concentration_dist(lines):
    import re as _re
    import json as _json
    pcts = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = _json.loads(ln)
        except ValueError:
            continue
        if r.get("event") != "screen":
            continue
        for note in r.get("reasons") or []:
            m = _re.search(r"top10 holds ([\d.]+)%", str(note))
            if m:
                pcts.append(float(m.group(1)))
                break
    if not pcts:
        return
    pcts.sort()
    n = len(pcts)
    med = pcts[n // 2]
    print(f"--- top10 concentration (n={n}, bar=50%) ---")
    print(f"  min {pcts[0]:.1f}% | median {med:.1f}% | max {pcts[-1]:.1f}%")
    for lo, hi, label in ((50, 55, "near-miss 50-55%"),
                          (55, 65, "over 55-65%"),
                          (65, 80, "over 65-80%"),
                          (80, 101, "over 80% (structurally held)")):
        k = sum(1 for v in pcts if lo <= v < hi or (hi == 101 and v >= 80))
        print(f"  {k:5d}  {label}")


def main():
    ap = argparse.ArgumentParser(description="TARS satellite runner (paper-only)")
    ap.add_argument("--loop", action="store_true", help="run continuously")
    ap.add_argument("--interval", type=float, default=60, help="seconds between cycles")
    ap.add_argument("--status", action="store_true", help="show portfolio and exit")
    ap.add_argument("--diagnose", action="store_true",
                    help="rejected_by histogram from the blotter and exit")
    ap.add_argument("--halt", action="store_true",
                    help="engage kill switch (touch HALT: no new entries) and exit")
    ap.add_argument("--resume", action="store_true",
                    help="release kill switch (remove HALT) and exit")
    ap.add_argument("--candidates", type=int, default=10, help="max candidates per cycle")
    ap.add_argument("--entry-size", type=float, default=25.0, help="paper entry size USD")
    ap.add_argument("--reentry-cooldown", type=float, default=45.0,
                    help="minutes a mint is blocked from re-entry after exit")
    ap.add_argument("--max-session", type=float, default=100.0,
                    help="session spend cap USD (must fit several base entries)")
    args = ap.parse_args()

    from src.meme_loop import SatelliteLoop
    loop = SatelliteLoop(
        entry_size_usd=args.entry_size,
        max_session_usd=args.max_session,
        max_candidates=args.candidates,
        reentry_cooldown_s=args.reentry_cooldown * 60.0,
    )

    if args.status:
        show_status(loop)
        return 0

    if args.diagnose:
        return show_diagnose()

    if args.halt:
        loop.halt_path.parent.mkdir(parents=True, exist_ok=True)
        loop.halt_path.touch()
        print(f"halt engaged: {loop.halt_path} (entries blocked, exits run)")
        return 0

    if args.resume:
        try:
            loop.halt_path.unlink()
            print("halt released: entries resume next cycle")
        except FileNotFoundError:
            print("halt was not engaged")
        return 0

    if args.loop:
        print(f"TARS satellite running (interval={args.interval}s, "
              f"entry=${args.entry_size}, candidates={args.candidates})")
        print("Paper-only. Press Ctrl+C to stop.\n")
        try:
            while True:
                try:
                    run_one_round(loop)
                except Exception as e:
                    print(f"  cycle error: {type(e).__name__}: {e}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            show_status(loop)
    else:
        run_one_round(loop)
        show_status(loop)

    return 0


if __name__ == "__main__":
    sys.exit(main())
