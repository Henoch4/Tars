"""RugCheck feed tests: normalization, fail-soft, no fabrication.
Transport faked (no network, no key)."""
import pytest

from src.meme_rugcheck import RugCheckError, rugcheck_mint


def _report(**kw):
    base = {"mint": "M", "creator": "C1", "creatorBalance": 0,
            "rugged": False, "score": 11400,
            "risks": [{"name": "Low amount of LP Providers", "level": "warn"}],
            "topHolders": [{"address": "C1", "pct": 3.5},
                            {"address": "X", "pct": 10.0}]}
    base.update(kw)
    return base


def test_full_report_normalizes():
    r = rugcheck_mint("M", get=lambda url: _report())
    assert r["creator_address"] == "C1"
    assert r["creator_hold_pct"] == 3.5
    assert r["rugcheck_rugged"] is False
    assert r["rugcheck_score"] == 11400.0
    assert r["rugcheck_risks"] == ["Low amount of LP Providers"]


def test_rugged_true_survives():
    r = rugcheck_mint("M", get=lambda url: _report(rugged=True))
    assert r["rugcheck_rugged"] is True


def test_absent_fields_are_none_not_zero():
    r = rugcheck_mint("M", get=lambda url: {"mint": "M"})
    assert r["creator_address"] is None
    assert r["creator_hold_pct"] is None
    assert r["rugcheck_rugged"] is None
    assert r["rugcheck_score"] is None
    assert r["rugcheck_risks"] is None


def test_creator_missing_from_holders_is_none():
    r = rugcheck_mint("M", get=lambda url: _report(
        topHolders=[{"address": "ZZZ", "pct": 50.0}]))
    assert r["creator_hold_pct"] is None


def test_transport_failure_raises_typed():
    def boom(url):
        raise ConnectionError("down")
    with pytest.raises(RugCheckError):
        rugcheck_mint("M", get=boom)


def test_non_object_report_raises_typed():
    with pytest.raises(RugCheckError):
        rugcheck_mint("M", get=lambda url: [1, 2, 3])


def test_invalid_mint_rejected():
    with pytest.raises(RugCheckError):
        rugcheck_mint("  ")
