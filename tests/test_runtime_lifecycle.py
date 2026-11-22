from src.runtime import TradingRuntime
from src.settings import Settings
from src.strategy_lifecycle import LifecycleState


class _Cli:
    async def run(self, *args, **kwargs):
        return {"data": []}


def test_runtime_exposes_canonical_paper_lifecycle(tmp_path):
    settings = Settings(dry_run=True,
                        execution_intent_db=str(tmp_path / "intents.sqlite3"))
    runtime = TradingRuntime(settings, "scheduler", cli=_Cli(), dry_run=True)
    status = runtime.lifecycle_status()
    assert status.state is LifecycleState.RUNNING
    assert status.dry_run is True
    assert status.live_enabled is False
