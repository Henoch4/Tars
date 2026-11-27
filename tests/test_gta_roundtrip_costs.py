from src.gta_roundtrip_costs import decompose_roundtrip

def test_missing_components_fail_closed_as_partial():
    r = decompose_roundtrip(input_lamports=1_000_000, output_lamports=976_272)
    assert r.aggregate_loss_pct == 2.3728
    assert r.decomposition_status == "partial"
    assert r.transfer_fee_raw is None
    assert "transfer_fee_not_authoritatively_observed" in r.unknown_reasons

def test_reported_components_are_preserved():
    r = decompose_roundtrip(input_lamports=1_000_000, output_lamports=990_000,
                            buy_quote={"feeLamports": 1000, "priceImpactPct": 0.2,
                                       "prioritizationFeeLamports": 500},
                            sell_quote={"feeLamports": 1200, "priceImpactPct": 0.3,
                                        "prioritizationFeeLamports": 700})
    assert r.swap_fee_lamports == 2200
    assert r.priority_fee_lamports == 1200
    assert r.price_impact_pct == 0.5
    assert r.decomposition_status == "partial"
