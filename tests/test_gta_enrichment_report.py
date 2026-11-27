from src.gta_enrichment_report import build_report


def test_report_counts_completeness_sources_and_missing_fields():
    report = build_report([
        {
            "mint": "a", "allowed": False,
            "enrichment_missing": ["lp_locked"],
            "enrichment_sources": {"helius": "ok"},
            "enrichment_errors": {},
            "reason": "safety enrichment incomplete",
        },
        {
            "mint": "b", "allowed": True,
            "enrichment_missing": [],
            "enrichment_sources": {"helius": "ok"},
            "enrichment_errors": {},
            "reason": "candidate passed GTA event policy",
        },
        {"mint": "legacy", "allowed": False, "reason": "old row"},
    ])
    assert report["observations"] == 3
    assert report["complete_safety_payloads"] == 1
    assert report["completeness_pct"] == round(100 / 3, 3)
    assert report["legacy_rows_without_completeness_metadata"] == 1
    assert report["field_missing"]["lp_locked"] == 2
    assert report["source_status"]["helius:ok"] == 2
    assert report["provider_quality"]["observations"] == 2
    assert report["provider_quality"]["complete"] == 2
