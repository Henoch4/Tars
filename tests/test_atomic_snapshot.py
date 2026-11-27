import pytest
from scripts import capture_atomic_snapshot as cap


def test_batches_require_common_context_slot(monkeypatch):
    slots=iter([10,10])
    def fake(*a):
        s=next(slots); return {"result":{"context":{"slot":s},"value":[{}]}}
    monkeypatch.setattr(cap, "rpc", fake)
    slot, rows=cap.fetch_batches("u", ["a","b"], batch_size=1)
    assert slot == 10 and len(rows) == 2


def test_mixed_context_is_rejected(monkeypatch):
    slots=iter([10,11])
    monkeypatch.setattr(cap, "rpc", lambda *a: {"result":{"context":{"slot":next(slots)},"value":[{}]}})
    with pytest.raises(RuntimeError, match="incoherent"):
        cap.fetch_batches("u", ["a","b"], batch_size=1)
