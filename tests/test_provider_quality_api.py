from src.provider_quality import provider_quality_from_jsonl


def test_provider_quality_jsonl_is_read_only_and_defensive(tmp_path):
    path = tmp_path / "blotter.jsonl"
    path.write_text('{"event":"screen","provider_statuses":{"helius":"complete"}}\nnot-json\n', encoding="utf-8")
    report = provider_quality_from_jsonl(path)
    assert report.observations == 1
    assert report.complete == 1


def test_provider_quality_missing_file_is_empty(tmp_path):
    report = provider_quality_from_jsonl(tmp_path / "missing.jsonl")
    assert report.observations == 0
    assert report.complete == 0
