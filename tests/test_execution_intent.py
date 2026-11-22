import pytest

from src.execution import ExecutionIntent, ExecutionReceipt, IntentRefused


def test_intent_is_dry_run_by_default():
    intent = ExecutionIntent.create(
        venue="solana", network="mainnet", action="buy", asset="Mint",
        amount="1000000", max_slippage_bps=100,
    )
    assert intent.dry_run is True
    assert intent.allow_live is False
    assert '"action":"buy"' in intent.canonical_json()


def test_live_requires_explicit_approval_and_not_dry_run():
    with pytest.raises(IntentRefused):
        ExecutionIntent.create(venue="solana", network="mainnet", action="buy",
                               asset="Mint", amount="1", max_slippage_bps=1,
                               allow_live=True)
    with pytest.raises(IntentRefused):
        ExecutionIntent.create(venue="solana", network="mainnet", action="buy",
                               asset="Mint", amount="1", max_slippage_bps=1,
                               dry_run=True, allow_live=True, operator_approved=True)


def test_invalid_amount_and_slippage_refuse():
    with pytest.raises(IntentRefused):
        ExecutionIntent.create(venue="solana", network="mainnet", action="buy",
                               asset="Mint", amount="0", max_slippage_bps=1)
    with pytest.raises(IntentRefused):
        ExecutionIntent.create(venue="solana", network="mainnet", action="buy",
                               asset="Mint", amount="1", max_slippage_bps=10001)


def test_receipt_cannot_claim_broadcast_without_signature():
    with pytest.raises(ValueError):
        ExecutionReceipt("i", "submitted", broadcast=True)


def test_refusal_is_explicit_and_sanitized():
    receipt = ExecutionReceipt.refused("i", "live gate disabled")
    assert receipt.status == "refused"
    assert receipt.broadcast is False
    assert receipt.signature is None
