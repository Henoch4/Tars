from src.product_status import product_status_map, product_status_payload


def test_product_status_covers_multiple_asset_classes_and_rails():
    areas = product_status_map()
    assert {area.asset_class for area in areas} >= {"Crypto majors", "Memecoins", "Utility tokens", "Equities"}
    assert {area.venue_or_chain for area in areas} >= {"Solana", "X Layer testnet", "Injective testnet"}


def test_product_status_is_read_only_and_gta_is_historical():
    payload = product_status_payload()
    assert payload["mode"] == "paper_shadow_only"
    assert payload["live_execution_enabled"] is False
    assert payload["gta_scope"] == "historical_analytics_only"
