from src.provider_quality import classify_satellite_rows, measure_provider_quality, ProviderObservation


def test_provider_quality_keeps_missing_metadata_unknown():
    report = classify_satellite_rows([{"event": "screen", "ts": 1}])
    assert report.observations == 1
    assert report.complete == 0
    assert report.status_counts["unknown"] == 1


def test_provider_quality_reports_rate_limits_and_conflicts():
    report = measure_provider_quality([
        ProviderObservation("helius", "complete"),
        ProviderObservation("helius", "rate_limited", reason="429"),
        ProviderObservation("rugcheck", "conflict", reason="authority mismatch"),
    ])
    assert report.completeness_pct == 33.333
    assert report.status_counts == {"complete": 1, "rate_limited": 1, "conflict": 1}
    assert report.failure_reasons["429"] == 1
