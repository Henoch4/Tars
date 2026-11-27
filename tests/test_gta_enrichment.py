from src.gta_enrichment import (validate_safety_payload,
                                combine_transfer_tax_evidence,
                                evidence_is_fresh)


def _payload():
    return {
        "mint": "m1", "liquidity_usd": 1000.0, "top10_hold_pct": 10.0,
        "holder_count": 200, "mint_authority_disabled": True,
        "freeze_authority_disabled": True, "honeypot": False,
        "transfer_tax_pct": 0.0, "bundled_buy_pct": 10.0,
        "creator_past_rugs": 0, "creator_launch_count": 2,
        "lp_locked": True,
    }


def test_complete_payload_passes_without_defaulting_any_field():
    result = validate_safety_payload(_payload())
    assert result.complete is True
    assert result.missing == ()
    assert result.invalid == ()


def test_missing_field_fails_closed():
    payload = _payload()
    del payload["lp_locked"]
    result = validate_safety_payload(payload)
    assert result.complete is False
    assert result.missing == ("lp_locked",)


def test_unsafe_field_is_invalid_even_when_present():
    payload = _payload()
    payload["honeypot"] = True
    payload["transfer_tax_pct"] = 2.0
    result = validate_safety_payload(payload)
    assert result.complete is False
    assert "honeypot" in result.invalid
    assert "transfer_tax_pct" in result.invalid


def test_token2022_zero_fee_is_authoritative():
    result = combine_transfer_tax_evidence(
        token2022={"transfer_tax_pct": 0.0})
    assert result.status == "authoritative_zero"
    assert result.sources == ("token2022",)


def test_simulation_never_becomes_authoritative_zero():
    result = combine_transfer_tax_evidence(simulation_tax_pct=0.0)
    assert result.status == "simulation_only"
    assert result.tax_pct == 0.0


def test_conflicting_sources_fail_closed():
    result = combine_transfer_tax_evidence(
        token2022={"transfer_tax_pct": 0.0}, jupiter_tax_pct=2.0)
    assert result.status == "unknown"
    assert result.reason == "conflicting authoritative values"


def test_stale_or_partial_evidence_fails_closed():
    assert not evidence_is_fresh({"status": "authoritative_zero"}, now=100)
    assert not evidence_is_fresh({"status": "conflicting", "observed_at": 100}, now=101)
    assert not evidence_is_fresh({"status": "authoritative_zero", "observed_at": 1}, now=1000)


def test_recent_nonconflicting_evidence_is_fresh():
    assert evidence_is_fresh({"status": "authoritative_zero", "observed_at": 100}, now=101)
