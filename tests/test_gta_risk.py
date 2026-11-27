import time
from datetime import datetime, timezone

from src.gta_event import GtaEventPolicy
from src.gta_risk import GtaRiskController


def _armed_policy():
    return GtaEventPolicy(
        enabled=True,
        live_enabled=True,
        start_utc=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2030, 1, 1, tzinfo=timezone.utc),
        max_total_usd=9,
        max_per_token_usd=3,
        max_open_positions=3,
    )


def test_risk_controller_enforces_budget_and_breakers():
    controller = GtaRiskController.create(_armed_policy())
    assert controller.entry_allowed(3, 0, time.time())[0] is True
    controller.record_entry(3)
    assert controller.entry_allowed(3, 1, time.time())[0] is True
    controller.record_entry(3)
    controller.record_entry(3)
    assert controller.entry_allowed(1, 3, time.time()) == (False, "event budget exceeded")
