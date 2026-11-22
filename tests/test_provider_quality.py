import pytest

from src.provider_quality import ProviderObservation, ProviderQualityRegistry, measure_provider_quality


def test_provider_quality_measures_completeness_and_failures():
    report = measure_provider_quality([
        ProviderObservation("helius", "complete", 1),
        ProviderObservation("helius", "rate_limited", 2, reason="429"),
        ProviderObservation("jupiter", "conflict", 3, reason="quote mismatch"),
    ])
    assert report.observations == 3
    assert report.complete == 1
    assert report.completeness_pct == 33.333
    assert report.rate_limited == 1
    assert report.conflicts == 1


def test_noncomplete_observation_requires_reason():
    with pytest.raises(ValueError):
        ProviderObservation("helius", "unknown", 1)


def test_registry_is_append_only_and_rebuilds_report(tmp_path):
    registry = ProviderQualityRegistry(tmp_path / "providers.jsonl")
    registry.record(ProviderObservation("helius", "complete", 1))
    registry.record(ProviderObservation("helius", "error", 2, reason="timeout"))
    report = registry.report()
    assert report.observations == 2
    assert report.status_counts["error"] == 1
