from src.execution import ExecutionIntent, plan_intent


def test_planner_is_read_only():
    intent = ExecutionIntent.create(
        venue="solana", network="mainnet", action="buy", asset="mint",
        amount="1", max_slippage_bps=100,
    )
    plan = plan_intent(intent)
    assert (plan.status, plan.signed, plan.broadcast) == ("planned", False, False)


def test_planner_never_accepts_live_path():
    intent = ExecutionIntent(
        "i", "solana", "mainnet", "buy", "mint", "1", 100,
        dry_run=False, allow_live=True, operator_approved=True,
    )
    plan = plan_intent(intent)
    assert plan.status == "refused"
