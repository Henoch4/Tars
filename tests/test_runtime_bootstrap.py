"""Regression coverage for the shared API/scheduler runtime boundary."""
from __future__ import annotations

import pytest

from src.runtime import DeferredOnchainLogger, RuntimeSafetyError, build_runtime
from src.execution.risk_gate import DurableDailyCounters
from src.settings import Settings


def _paper_settings(tmp_path):
    return Settings(
        dry_run=True,
        allow_live=False,
        risk_state_path=str(tmp_path / "risk.json"),
        audit_log_path=str(tmp_path / "audit.jsonl"),
        multi_leg_state_dir=str(tmp_path / "packages"),
        execution_claim_dir=str(tmp_path / "claims"),
        execution_intent_db=str(tmp_path / "intents.sqlite3"),
    )


def test_deferred_logger_constructs_once_and_only_on_connect():
    made = []

    class Fake:
        value = 7

    logger = DeferredOnchainLogger(lambda: made.append(Fake()) or made[-1])
    assert made == []
    assert logger.connect().value == 7
    assert logger.value == 7
    assert len(made) == 1


def test_paper_runtime_has_shared_safety_components(tmp_path):
    runtime = build_runtime(_paper_settings(tmp_path), role="scheduler", cli=object())
    assert runtime.dry_run is True
    assert runtime.multi_leg_manager is None
    assert runtime.agent.curator is runtime.curator
    assert runtime.agent.integrity_gate is runtime.integrity_gate
    assert runtime.status()["runtime_initialized"] is True


def test_live_runtime_requires_explicit_audit_configuration(tmp_path):
    settings = _paper_settings(tmp_path).model_copy(update={
        "dry_run": False,
        "allow_live": True,
        "xlayer_rpc_url": "",
        "audit_contract_address": "",
        "agent_wallet_private_key": "",
    })
    with pytest.raises(RuntimeSafetyError, match="audit"):
        build_runtime(settings, role="api", cli=object())


def test_execution_mode_drift_is_blocked(tmp_path):
    runtime = build_runtime(_paper_settings(tmp_path), role="api", cli=object())
    runtime.agent.executor.dry_run = False
    with pytest.raises(RuntimeSafetyError, match="mode changed"):
        runtime.ensure_ready()


def test_durable_counter_instances_refresh_before_mutation(tmp_path):
    path = str(tmp_path / "shared.json")
    first = DurableDailyCounters(path=path)
    second = DurableDailyCounters(path=path)
    first.increment("agent:day", "trade_count", 1)
    second.increment("agent:day", "trade_count", 1)
    assert DurableDailyCounters(path=path).snapshot("agent:day")["trade_count"] == 2


def test_shared_runtime_uses_fail_closed_counter_persistence(tmp_path):
    runtime = build_runtime(_paper_settings(tmp_path), role="api", cli=object())
    assert runtime.risk_gate._counters.fail_closed is True
