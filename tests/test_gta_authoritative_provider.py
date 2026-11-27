from src.gta_external_safety import parse_authoritative_sellability, compare_sellability_evidence

def test_explicit_provider_result_preserved():
    e = parse_authoritative_sellability({"sellability":"sellable","test_conditions":"funded simulation","reason":"sell succeeded"}, source="provider", observed_at=100, observation_slot=7)
    assert e.status == "sellable" and e.observation_slot == 7

def test_generic_score_is_unknown():
    e = parse_authoritative_sellability({"risk_score": 12}, source="provider", observed_at=100)
    assert e.status == "unknown"

def test_conflict_blocks_promotion():
    e = parse_authoritative_sellability({"sellability":"sellable","test_conditions":"x","reason":"ok"}, source="provider", observed_at=100)
    assert compare_sellability_evidence(e, token2022_sell_restrictions="unknown", replay_sell_ok=False).status == "unknown"
