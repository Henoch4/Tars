from src.execution import ExecutionIntent, ReadOnlyExecutionBackend


def _intent():
    return ExecutionIntent.create(
        venue="solana", network="mainnet", action="buy", asset="mint",
        amount="1", max_slippage_bps=100,
    )


def test_backend_never_signs_or_broadcasts():
    receipt = ReadOnlyExecutionBackend().execute(_intent())
    assert receipt.status == "refused"
    assert receipt.signed is False
    assert receipt.broadcast is False
    assert receipt.signature is None


def test_backend_plan_is_deterministic_boundary():
    plan = ReadOnlyExecutionBackend().plan(_intent())
    assert plan.status == "planned"
    assert plan.signed is False
    assert plan.broadcast is False
