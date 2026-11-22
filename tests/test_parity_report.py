from src.parity_report import compare_decisions


def test_parity_report_explains_matching_and_different_decisions():
    report = compare_decisions([
        {"paper": "hold", "agent": "hold"},
        {"paper": "buy", "agent": "hold"},
    ])
    assert report.samples == 2
    assert report.matching_decisions == 1
    assert report.differing_decisions == 1
    assert report.match_rate == 0.5
    assert report.differences[0]["index"] == 1
