import pytest

from src.experiment_schema import ExperimentRecord


def test_experiment_record_is_paper_by_default():
    record = ExperimentRecord.planned(
        experiment_id="age_variant_v1", version="1", name="Age filter",
        hypothesis="A maturity filter improves outcomes", sample_target=500,
    )
    assert record.mode == "paper"
    assert record.state == "planned"
    assert '"experiment_id":"age_variant_v1"' in record.canonical_json()


def test_live_mode_is_rejected_at_schema_boundary():
    with pytest.raises(ValueError):
        ExperimentRecord("x", "1", "x", "h", "planned", "live", 1)


def test_complete_requires_evidence():
    with pytest.raises(ValueError):
        ExperimentRecord("x", "1", "x", "h", "complete", "paper", 1)
