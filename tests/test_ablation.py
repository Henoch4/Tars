"""Ablation table regression (rule 4): feed roles must not silently change.

If a feed's fields move (new gate reads them, or stops reading them),
the flip counts move and this test names the shift. Semantics pinned:
helius = chain truth (holders/authorities), dexscreener = graduation +
market state, rugcheck = rugged verdict + insider share.
"""
import scripts.meme_ablate as ablate


def test_baseline_battery_verdicts():
    baseline, _ = ablate.ablate()
    assert baseline["clean-pass"].passed is True
    assert baseline["concentrated"].rejected_by == "holder_concentration"
    assert baseline["few-holders"].rejected_by == "too_few_holders"
    assert baseline["ungraduated"].rejected_by == "unconfirmed"
    assert baseline["thin"].rejected_by == "thin_liquidity"
    assert baseline["taxed"].rejected_by == "honeypot_tax"
    assert baseline["rugged-flag"].rejected_by == "rugged_confirmed"


def test_flip_counts_per_feed():
    _, report = ablate.ablate()
    assert len(report["helius"]) == 6
    assert len(report["dexscreener"]) == 4
    assert len(report["rugcheck"]) == 4


def test_rugcheck_supplies_insider_gate():
    # DexScreener has no creator share: dropping rugcheck must flip the
    # insider check to missing_data (this is the feed's measured value).
    _, report = ablate.ablate()
    thin_flips = [f for f in report["rugcheck"] if f[0] == "thin"]
    assert thin_flips and thin_flips[0][2] == "missing_data"


def test_helius_outage_kills_everything():
    # Chain truth for holders/authorities: no helius, no passes, no
    # specific verdicts — everything degrades to missing_data.
    _, report = ablate.ablate()
    assert len(report["helius"]) == 6
    assert {after for _, _, after in report["helius"]} == {"missing_data"}
