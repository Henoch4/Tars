import pytest
from src.gta_external_safety import AuthoritativeSellabilityEvidence, validate_authoritative_sellability

def test_sellable_requires_fresh_conditions():
    e = AuthoritativeSellabilityEvidence("provider", "sellable", 100, 7, "simulated round trip", "explicit sell succeeded")
    assert validate_authoritative_sellability(e, now=200).status == "sellable"

def test_stale_is_not_sellable():
    e = AuthoritativeSellabilityEvidence("provider", "sellable", 100, 7, "round trip", "ok")
    assert validate_authoritative_sellability(e, now=1101).status == "stale"

def test_unknown_and_provider_error_are_preserved():
    for status in ("unknown", "provider_error", "unsupported"):
        e = AuthoritativeSellabilityEvidence("provider", status, 100, reason="no verdict")
        assert validate_authoritative_sellability(e, now=100).status == status

def test_invalid_status_rejected():
    with pytest.raises(ValueError):
        validate_authoritative_sellability(AuthoritativeSellabilityEvidence("p", "safe", 1, reason="x"), now=1)
