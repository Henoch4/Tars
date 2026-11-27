import json

from scripts.meme_window_report import build_report


def test_report_is_sanitized_and_reviewable(tmp_path):
    source = tmp_path / "window.jsonl"
    source.write_text("\n".join([
        json.dumps({"event": "entry", "mint": "M", "ts": 1}),
        json.dumps({"event": "screen", "mint": "M", "ts": 1}),
        json.dumps({"event": "exit", "mint": "M", "reason": "TIME_BOX", "net_usd": -1, "ts": 2}),
    ]) + "\n", encoding="utf-8")
    report = build_report(source)
    assert report["artifact_type"] == "meme_satellite_window_metrics"
    assert report["read_only"] is True
    assert report["metrics"]["net_usd"] == -1
    assert report["metrics"]["provider_quality"]["complete"] == 0
    assert report["metrics"]["provider_quality"]["status_counts"]["unknown"] == 1
    assert "mint" not in json.dumps(report)
