"""Feed ablation harness (rule 4): which feeds can change verdicts.

Turn each feed OFF (null its fields) across a battery of snapshots and
count verdict flips vs baseline. No network, no loop changes, no restarts:
pure import of the filter + synthetic snapshots. Answers "which tools do
work" statically; live-data replay (recorded snapshots) is phase 2 and
needs a blotter schema change — deliberately not done here.

Feed -> fields map (must match _enrich + snapshot_for_mint wiring):
  helius      top10_hold_pct, holder_count, mint/freeze_authority_renounced
  dexscreener age_s, liquidity_usd, graduated (+price, not a filter input)
  rugcheck    creator_address, creator_hold_pct, rugcheck_rugged/score
  history     creator_past_rugs has NO source (always None) — nothing to
              ablate; the gap is tracked, not hidden.

Usage: py -3 scripts/meme_ablate.py
Exit 0 always (report, not gate); CI pins the table in test_ablation.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FEED_FIELDS = {
    "helius": ["top10_hold_pct", "holder_count",
               "mint_authority_renounced", "freeze_authority_renounced"],
    "dexscreener": ["age_s", "liquidity_usd", "graduated", "mcap_usd"],
    "rugcheck": ["creator_address", "creator_hold_pct",
                 "rugcheck_rugged", "rugcheck_score"],
}


def _base(**overrides):
    from src.meme_satellite import TokenSnapshot
    snap = dict(
        mint="M", top10_hold_pct=20.0, holder_count=25,
        creator_address="C1", rugcheck_rugged=False, rugcheck_score=100.0,
        creator_hold_pct=3.0, lp_locked=True,
        mint_authority_renounced=True, freeze_authority_renounced=True,
        age_s=48 * 3600, liquidity_usd=100_000.0,
        buy_tax_pct=1.0, sell_tax_pct=2.0,
        creator_past_rugs=0, graduated=True, mcap_usd=250_000.0)
    snap.update(overrides)
    return TokenSnapshot(**snap)


def battery():
    """Snapshots spanning every gate boundary (pass + each rejection)."""
    return {
        "clean-pass": _base(),
        "concentrated": _base(top10_hold_pct=80.0),
        "few-holders": _base(holder_count=6),
        "ungraduated": _base(graduated=False),
        "thin": _base(liquidity_usd=5_000.0),
        "taxed": _base(buy_tax_pct=12.0),
        "rugged-flag": _base(rugcheck_rugged=True),
    }


def ablate(battery_snapshots=None):
    """Drop each feed's fields across the battery; count verdict flips."""
    from src.meme_satellite import scream_filter
    battery_snapshots = battery_snapshots or battery()
    baseline = {name: scream_filter(s) for name, s in battery_snapshots.items()}
    report = {}
    for feed, fields in FEED_FIELDS.items():
        flips = []
        for name, snap in battery_snapshots.items():
            import dataclasses
            dropped = dataclasses.replace(
                snap, **{f: None for f in fields})
            verdict = scream_filter(dropped)
            base = baseline[name]
            if verdict.passed != base.passed or \
                    verdict.rejected_by != base.rejected_by:
                flips.append((name, base.rejected_by, verdict.rejected_by))
        report[feed] = flips
    return baseline, report


def main():
    baseline, report = ablate()
    print("baseline:", {k: (v.passed, v.rejected_by) for k, v in baseline.items()})
    for feed, flips in report.items():
        print(f"-- {feed}: {len(flips)} flips")
        for name, before, after in flips:
            print(f"   {name}: {before} -> {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
