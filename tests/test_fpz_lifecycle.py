import pytest

from src.fpz_lifecycle import FpzLifecycleLedger


def test_fpz_ledger_uses_actual_quantities_and_costs():
    ledger = FpzLifecycleLedger(1000, filled_notional=900,
                                exit_notional=900, settled_funding=12, fees=3)
    assert ledger.remaining_notional == 0
    assert ledger.closed is True
    assert ledger.realized_pnl == 9


def test_fpz_ledger_rejects_overfill_and_overexit():
    with pytest.raises(ValueError):
        FpzLifecycleLedger(1000, filled_notional=1001)
    with pytest.raises(ValueError):
        FpzLifecycleLedger(1000, filled_notional=500, exit_notional=501)


def test_fpz_lifecycle_transitions_are_immutable_and_quantity_first():
    initial = FpzLifecycleLedger(1000)
    open_ledger = initial.record_fill(600).record_funding(8).record_fee(2)
    partial = open_ledger.record_exit(250)
    assert initial.filled_notional == 0
    assert open_ledger.remaining_notional == 600
    assert partial.remaining_notional == 350
    assert partial.realized_pnl == -344
