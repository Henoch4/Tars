from src.parity_evidence import parity_evidence
from src.parity_report import compare_decisions


def test_parity_evidence_is_reviewable_and_read_only():
    evidence = parity_evidence(
        experiment_id="fpz-parity-v1",
        input_source="fixtures/funding-periods.jsonl",
        report=compare_decisions([{"paper": "hold", "agent": "hold"}]),
    )
    assert evidence["artifact_type"] == "paper_agent_parity"
    assert evidence["read_only"] is True
    assert evidence["match_rate"] == 1.0
    assert "observed_at" in evidence
