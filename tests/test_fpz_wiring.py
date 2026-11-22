"""R4 regression tests — funding_persistence_z live wiring.

Contract: docs/fpz_live_wiring_spec.md (SP-1 signal priority, EB-1 ensemble
bypass, AC-1 arb-package coexistence, RG-1 risk gate unchanged, §4.4 audit).

Fully hermetic: stub CLI, no network, no keys.

NOTE on the SafetyNet stub used by the pipeline tests below. These tests stub
SafetyNet deliberately to isolate the fpz wiring contract and let the signal
reach the RiskGate, which is what this file measures. The real SafetyNet has
its own unit-contract regressions in `tests/test_safety_net.py`.
"""
from __future__ import annotations

import json
import time

import pytest

from src.agent import AutonomousTradingAgent
from src.audit_trail import AuditLog
from src.execution import RiskGate, RiskCheckResult
from src.safety_net import SafetyNetResult
from src.signals import Signal, ensemble_signal

SOL = "SOL-USDT-SWAP"
BTC = "BTC-USDT-SWAP"

# Varied funding history (>mild band, non-zero std) so fpz can compute a
# z-score. A constant series yields std=0 -> NEUTRAL (correctly).
FPZ_HIST = [
    0.001, 0.0012, 0.0009, 0.0015, 0.0011, 0.0013, 0.001, 0.0014,
    0.0012, 0.0016, 0.0011, 0.0013, 0.001, 0.0012, 0.0014, 0.0011,
    0.0013, 0.0012, 0.0015, 0.0012,
]


class _StubCli:
    """Minimal OkxCli stand-in — never touches the network."""

    async def run(self, *args, **kwargs):
        return {"data": []}


class _FakeManager:
    """MultiLegExecutionManager stand-in: capacity is whatever the test says."""

    def __init__(self, allowed: bool = True):
        self.allowed = allowed
        self.calls: list[str] = []

    def can_open(self, asset: str):
        self.calls.append(asset)
        return (self.allowed, None if self.allowed else "capacity full")


class _RecordingGate(RiskGate):
    """Real gate, but counts how many times check_order was consulted."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.check_calls = 0

    def check_order(self, order, agent_id="default", **kwargs):
        self.check_calls += 1
        return super().check_order(order, agent_id, **kwargs)


class _RejectingGate(RiskGate):
    """Real gate that refuses everything — proves fpz has no private bypass."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.check_calls = 0

    def check_order(self, order, agent_id="default", **kwargs):
        self.check_calls += 1
        return RiskCheckResult(
            approved=False, code="POSITION_TOO_LARGE", reason="test rejection"
        )


def _md(asset: str = SOL, funding: float = 0.004, price: float = 150.0) -> dict:
    return {
        "asset": asset,
        "trades": [{"px": str(price), "sz": "1"}],
        "funding_rate": funding,
        "position": None,
        "timestamp": time.time(),
    }


def _agent(seed_sol: bool = True, **overrides) -> AutonomousTradingAgent:
    kwargs = dict(
        okx_cli=_StubCli(),
        risk_gate=RiskGate(max_position_usd=5000),
        dry_run=True,
        max_position_usd=5000,
    )
    kwargs.update(overrides)
    agent = AutonomousTradingAgent(**kwargs)
    if seed_sol:
        agent._ml_history[SOL] = {
            "funding": list(FPZ_HIST),
            "prices": [150.0 + i * 0.01 for i in range(len(FPZ_HIST))],
        }
    return agent


def _patch_spot_fetch(agent: AutonomousTradingAgent, price: float = 150.0) -> None:
    """_process_asset fetches the spot companion leg; keep it offline."""

    async def _fetch(asset: str, *a, **k):
        return _md(asset, funding=0.0, price=price)

    agent._fetch_market_data = _fetch  # type: ignore[method-assign]


def _approve_safety_net(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.safety_net.safety_net",
        lambda *a, **k: SafetyNetResult(
            approved=True, reason="stubbed for fpz wiring test", size_usd=10000,
            stop_pct=0.01, take_pct=0.015,
        ),
    )


async def _process(agent: AutonomousTradingAgent, md: dict) -> dict:
    return await agent._process_asset(md, "cycle_fpz_test", object())


def _fpz_entry(out: dict) -> dict:
    """The fpz signal as recorded in the individual-signal list."""
    entries = [
        s for s in out["signals"][0]["individual"]
        if s["strategy"] == "funding_persistence_z"
    ]
    assert entries, f"no fpz signal recorded; got {[s['strategy'] for s in out['signals'][0]['individual']]}"
    return entries[0]


def _patch_fpz(monkeypatch, **signal_kwargs) -> None:
    """Replace the fpz strategy with a fixed signal (still the same strategy id)."""

    def _fake(asset: str, funding_rate: float, funding_history=None, price_history=None, **kwargs):
        return Signal(
            strategy="funding_persistence_z",
            asset=asset,
            direction=signal_kwargs.get("direction", "SHORT"),
            confidence_bps=signal_kwargs.get("confidence_bps", 8000),
            entry_price=signal_kwargs.get("entry_price", 150.0),
            rationale="patched fpz",
            metadata={"funding_rate": funding_rate, "funding_z": 3.0, "persistence": 1.0},
        )

    monkeypatch.setattr("src.agent.funding_persistence_z_signal", _fake)


# ---------------------------------------------------------------------------
# 1. The inert default
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fpz_disabled_keeps_current_behavior():
    """fpz_live_enabled=False must reproduce pre-wiring behavior exactly."""
    agent = _agent(multi_leg_manager=_FakeManager())
    # (a) the arb package keeps priority at extreme funding when fpz is off
    assert agent._funding_arb_opportunity(
        SOL, _md(funding=0.02), spot_price=150.0, perp_price=150.0
    ) is True
    # (b) no fpz decision is ever selected
    assert agent._fpz_decision_signal(SOL, [Signal(
        strategy="funding_persistence_z", asset=SOL, direction="SHORT",
        confidence_bps=9200, entry_price=150.0)]) is None
    # (c) the per-cycle payload is unchanged too: pre-wiring shape, no fpz keys
    agent2 = _agent()
    _patch_spot_fetch(agent2)
    out = await agent2._process_asset(_md(), "cycle_fpz_test", object())
    assert set(out["signals"][0]) == {"asset", "ensemble", "individual"}
    assert out["signals"][0]["ensemble"]["strategy"] == "ensemble"


# ---------------------------------------------------------------------------
# 2. Ensemble bypass (EB-1 / SP-1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fpz_bypasses_ensemble_for_sol(monkeypatch):
    _approve_safety_net(monkeypatch)
    agent = _agent(fpz_live_enabled=True, fpz_bypass_ensemble=True)
    _patch_spot_fetch(agent)

    out = await _process(agent, _md())

    fpz = _fpz_entry(out)
    decision = out["signals"][0]["decision"]
    assert decision["strategy"] == "funding_persistence_z"
    assert decision["direction"] == fpz["direction"] == "SHORT"
    assert decision["confidence_bps"] == fpz["confidence_bps"]
    # The bypass matters: the ensemble divides confidence across signals, so
    # its number is strictly lower than the standalone fpz signal's.
    assert out["signals"][0]["ensemble"]["confidence_bps"] < fpz["confidence_bps"]
    assert out["signals"][0]["fpz_wiring"]["fpz_selected"] is True
    # Observability: the other strategies were still computed, just not voting.
    assert len(out["signals"][0]["individual"]) >= 3


@pytest.mark.asyncio
async def test_fpz_ensemble_member_when_bypass_off():
    """EB-1: bypass off -> fpz is one member and can be outvoted."""
    agent = _agent(fpz_live_enabled=True, fpz_bypass_ensemble=False)
    signals = [
        Signal(strategy="funding_persistence_z", asset=SOL, direction="SHORT",
               confidence_bps=9000, entry_price=150.0),
        Signal(strategy="funding_rate", asset=SOL, direction="LONG",
               confidence_bps=9500, entry_price=150.0),
    ]
    # no pre-emption...
    assert agent._fpz_decision_signal(SOL, signals) is None
    # ...and the ensemble vote lets funding_rate outvote fpz.
    combined = ensemble_signal(SOL, signals)
    assert combined.direction == "LONG"

    _patch_spot_fetch(agent)
    out = await agent._process_asset(_md(), "cycle_fpz_test", object())
    assert out["signals"][0]["decision"]["strategy"] == "ensemble"
    assert out["signals"][0]["fpz_wiring"]["fpz_selected"] is False


# ---------------------------------------------------------------------------
# 3. Arb-package coexistence (AC-1)
# ---------------------------------------------------------------------------

def test_arb_package_blocked_in_extreme_funding():
    agent = _agent(
        fpz_live_enabled=True, fpz_arb_package_priority=False,
        fpz_arb_block_band_bps=150.0, multi_leg_manager=_FakeManager(),
    )
    # 200 bps >= 150 bps band, and well above funding_arb_min_rate
    assert agent._funding_arb_opportunity(
        SOL, _md(funding=0.02), spot_price=150.0, perp_price=150.0
    ) is False


def test_arb_package_allowed_below_band():
    manager = _FakeManager()
    agent = _agent(
        fpz_live_enabled=True, fpz_arb_package_priority=False,
        fpz_arb_block_band_bps=150.0, multi_leg_manager=manager,
    )
    # 15 bps: above the 10 bps arb floor, far below the 150 bps block band
    assert agent._funding_arb_opportunity(
        SOL, _md(funding=0.0015), spot_price=150.0, perp_price=150.0
    ) is True
    assert manager.calls == [SOL]


def test_arb_package_priority_preserved_when_flag_true():
    """The flag must be what decides, so compare both settings at one funding.

    Asserting only the priority=True case would pass even if the block were a
    no-op (funding 0.02 also clears funding_arb_min_rate on its own).
    """
    kwargs = dict(
        fpz_live_enabled=True, fpz_arb_block_band_bps=150.0,
        multi_leg_manager=_FakeManager(),
    )
    preserved = _agent(fpz_arb_package_priority=True, **kwargs)
    default_priority = _agent(fpz_arb_package_priority=False, **kwargs)

    md = _md(funding=0.02)
    assert preserved._funding_arb_opportunity(
        SOL, md, spot_price=150.0, perp_price=150.0
    ) is True
    assert default_priority._funding_arb_opportunity(
        SOL, md, spot_price=150.0, perp_price=150.0
    ) is False


# ---------------------------------------------------------------------------
# 4. Confidence floor (§5.4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fpz_confidence_floor_blocks_weak_signal(monkeypatch, tmp_path):
    _approve_safety_net(monkeypatch)
    _patch_fpz(monkeypatch, confidence_bps=6500)  # tradeable but below 7000
    audit_path = tmp_path / "audit.jsonl"
    agent = _agent(
        fpz_live_enabled=True, fpz_confidence_floor_bps=7000,
        audit_log=AuditLog(audit_path),
    )
    _patch_spot_fetch(agent)

    out = await _process(agent, _md())

    assert out["decisions"] == [] and out["executions"] == []
    assert out["signals"][0]["decision"]["confidence_bps"] == 6500
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fpz_events = [e for e in events if e.get("event_type") == "fpz_decision"]
    assert fpz_events, f"no fpz_decision audit record; events={[e.get('event_type') for e in events]}"
    assert any(
        e["payload"].get("rejected_by") == "confidence_floor"
        for e in fpz_events
    )


# ---------------------------------------------------------------------------
# 5. D3 fail-closed entry price
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fpz_neutral_abstains_without_floor_rejection(tmp_path):
    """An fpz abstention is NO TRADE, not a confidence-floor rejection and not
    a hand-off to the ensemble.

    The fixture is built so the ENSEMBLE is non-neutral (funding_rate fires
    SHORT at 0.002) while fpz abstains (constant history -> std 0). If the
    code ever fell back to the ensemble on a NEUTRAL fpz, `decision` would
    read "ensemble"/SHORT here and this test fails.
    """
    audit_path = tmp_path / "audit.jsonl"
    agent = _agent(fpz_live_enabled=True, audit_log=AuditLog(audit_path))
    _patch_spot_fetch(agent)
    agent._ml_history[SOL] = {"funding": [0.002] * 20, "prices": [150.0] * 20}

    out = await _process(agent, _md(funding=0.002))

    rec = out["signals"][0]
    assert rec["decision"]["strategy"] == "funding_persistence_z"
    assert rec["decision"]["direction"] == "NEUTRAL"
    assert rec["fpz_wiring"]["fpz_selected"] is True
    # The premise: the ensemble would have traded, so a fallback is detectable.
    assert rec["ensemble"]["direction"] != "NEUTRAL"
    assert out["decisions"] == [] and out["executions"] == []

    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fpz_records = [e for e in events if e.get("event_type") == "fpz_decision"]
    # The abstention is still recorded (§4.4: every decision), attributed to
    # the abstain itself — not to SafetyNet or the confidence floor.
    assert fpz_records and fpz_records[-1]["payload"]["rejected_by"] == "abstained"
    assert not any(
        e["payload"].get("rejected_by") == "confidence_floor"
        for e in fpz_records
    )


@pytest.mark.asyncio
async def test_fpz_rejects_missing_price_d3(monkeypatch):
    _approve_safety_net(monkeypatch)
    _patch_fpz(monkeypatch, confidence_bps=8000, entry_price=None)
    gate = _RecordingGate(max_position_usd=5000)
    agent = _agent(fpz_live_enabled=True, risk_gate=gate)
    _patch_spot_fetch(agent)

    out = await _process(agent, _md())

    assert out["signals"][0]["decision"]["entry_price"] is None
    assert out["decisions"] == []
    assert out["executions"] == []
    # D3 is the *construction* guard: if it were removed, an OrderRequest
    # would be built and the gate would be consulted (then rejected for
    # another reason). Zero calls proves no order was ever constructed.
    assert gate.check_calls == 0


# ---------------------------------------------------------------------------
# 6. Curator profile eligibility is part of scope
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fpz_not_wired_when_curator_profile_excludes_it():
    """A profile without funding_persistence_z must disable the whole wiring.

    Otherwise AC-1 would block the delta-neutral package for SOL while
    yielding to a signal the curator already dropped from the cycle — worse
    than either behavior alone.
    """
    from pathlib import Path

    from src.curator import CuratorAgent

    profiles = Path(__file__).resolve().parent.parent / "config" / "profiles.yaml"
    curator = CuratorAgent(profiles)
    curator.state.current_profile = "conservative"
    agent = _agent(
        fpz_live_enabled=True, curator=curator, multi_leg_manager=_FakeManager()
    )
    agent._resolve_curator_profile()
    assert agent._enabled_signals == {"funding_rate", "ml_funding_carry"}

    # The arb package is NOT blocked for a signal that cannot exist...
    assert agent._fpz_blocks_funding_arb(SOL, 0.02) is False
    assert agent._funding_arb_opportunity(
        SOL, _md(funding=0.02), spot_price=150.0, perp_price=150.0
    ) is True
    # ...and no fpz decision is selected even when one is offered.
    offered = Signal(
        strategy="funding_persistence_z", asset=SOL, direction="SHORT",
        confidence_bps=9200, entry_price=150.0,
    )
    assert agent._fpz_decision_signal(SOL, [offered]) is None


# ---------------------------------------------------------------------------
# 7. Non-SOL assets untouched (fpz_sol_only)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fpz_non_sol_assets_unchanged():
    md = _md(asset=BTC, funding=0.004, price=100.0)

    off = _agent(fpz_live_enabled=False)
    _patch_spot_fetch(off, price=100.0)
    out_off = await off._process_asset(dict(md), "cycle_fpz_test", object())

    on = _agent(fpz_live_enabled=True, fpz_sol_only=True)
    _patch_spot_fetch(on, price=100.0)
    out_on = await on._process_asset(dict(md), "cycle_fpz_test", object())

    assert out_on["signals"][0] == out_off["signals"][0]


def test_sol_only_false_does_not_extend_arb_block_to_other_assets():
    """`fpz_sol_only=False` must not starve non-SOL assets of the package.

    fpz is only emitted for SOL (src/agent.py `_generate_signals`), so
    extending AC-1's band block to BTC/ETH would block the delta-neutral
    package while yielding to a signal that cannot exist.
    """
    agent = _agent(
        fpz_live_enabled=True, fpz_sol_only=False,
        multi_leg_manager=_FakeManager(),
    )
    assert agent._fpz_blocks_funding_arb(BTC, 0.02) is False
    assert agent._funding_arb_opportunity(
        BTC, _md(asset=BTC, funding=0.02, price=100.0), 100.0, 100.0
    ) is True
    # SOL is still wired under the same flags.
    assert agent._fpz_blocks_funding_arb(SOL, 0.02) is True


@pytest.mark.asyncio
async def test_arb_blocked_before_fpz_has_history():
    """AC-1 is unconditional: at extreme funding the package is blocked even
    when fpz cannot signal yet (cold start <5 points -> NEUTRAL).

    Spec §5.3 chose this. Pinned so the "neither the package nor a directional
    trade runs" window after a restart is a deliberate decision, not a
    surprise — see spec §13.
    """
    agent = _agent(
        seed_sol=False, fpz_live_enabled=True, multi_leg_manager=_FakeManager()
    )
    _patch_spot_fetch(agent)

    assert agent._funding_arb_opportunity(
        SOL, _md(funding=0.02), 150.0, 150.0
    ) is False

    out = await _process(agent, _md(funding=0.02))
    assert out["signals"][0]["decision"]["strategy"] == "funding_persistence_z"
    assert out["signals"][0]["decision"]["direction"] == "NEUTRAL"
    assert out["decisions"] == []


# ---------------------------------------------------------------------------
# 7. The gate is still the gate (RG-1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_risk_gate_still_applies_to_fpz(monkeypatch, tmp_path):
    _approve_safety_net(monkeypatch)
    gate = _RecordingGate(max_position_usd=5000)
    audit_path = tmp_path / "audit.jsonl"
    agent = _agent(
        fpz_live_enabled=True, risk_gate=gate, audit_log=AuditLog(audit_path)
    )
    _patch_spot_fetch(agent)

    out = await _process(agent, _md())

    # Two consults prove the pre-flight gate ran AS WELL AS the executor's
    # submission re-check: removing the pre-flight `check_order` in
    # `_process_asset` drops this to 1 and fails here.
    assert gate.check_calls >= 2, (
        f"expected pre-flight + executor gate consults, got {gate.check_calls}"
    )
    assert out["signals"][0]["decision"]["strategy"] == "funding_persistence_z"
    assert out["decisions"], f"expected an approved decision; errors={out['errors']}"

    # §4.4: an APPROVED fpz decision leaves a record with rejected_by=None and
    # the full documented field set (no rejection is claimed on a pass).
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    approved = [
        e["payload"] for e in events
        if e.get("event_type") == "fpz_decision" and e["payload"]["rejected_by"] is None
    ]
    assert approved, f"no approved fpz_decision record; events={[e.get('event_type') for e in events]}"
    payload = approved[0]
    for field in (
        "cycle_id", "asset", "strategy", "direction", "confidence_bps",
        "funding_z", "persistence", "funding_rate", "entry_price", "size_usd",
        "bypass_ensemble", "arb_package_blocked", "rejected_by",
    ):
        assert field in payload, f"§4.4 field missing from approved record: {field}"
    assert payload["funding_z"] is not None and payload["persistence"] is not None
    assert payload["size_usd"] > 0


@pytest.mark.asyncio
async def test_fpz_order_rejected_by_risk_gate_is_not_executed(monkeypatch, tmp_path):
    _approve_safety_net(monkeypatch)
    gate = _RejectingGate(max_position_usd=5000)
    audit_path = tmp_path / "audit.jsonl"
    agent = _agent(
        fpz_live_enabled=True, risk_gate=gate, audit_log=AuditLog(audit_path)
    )
    _patch_spot_fetch(agent)

    out = await _process(agent, _md())

    # Exactly one consult: the pre-flight refusal ends the asset before the
    # executor is reached. (Had the pre-flight been skipped, the executor's
    # re-check would reject, but `decisions` would already be non-empty —
    # caught by the assertion below.)
    assert gate.check_calls == 1
    assert out["decisions"] == []
    assert out["executions"] == []
    assert any("Risk gate rejected" in e for e in out["errors"])
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(
        e.get("event_type") == "fpz_decision"
        and e["payload"].get("rejected_by") == "risk_gate"
        for e in events
    )


# ---------------------------------------------------------------------------
# 8. Settings knobs are declared once, with the spec's defaults
# ---------------------------------------------------------------------------

def test_fpz_settings_defaults_match_spec():
    from src.settings import Settings

    s = Settings()
    assert s.fpz_live_enabled is False
    assert s.fpz_sol_only is True
    assert s.fpz_bypass_ensemble is True
    assert s.fpz_confidence_floor_bps == 7000
    assert s.fpz_arb_package_priority is False
    assert s.fpz_arb_block_band_bps == 150.0


def test_fpz_settings_env_overrides(monkeypatch):
    from src.settings import Settings

    monkeypatch.setenv("FPZ_LIVE_ENABLED", "true")
    monkeypatch.setenv("FPZ_CONFIDENCE_FLOOR_BPS", "8200")
    monkeypatch.setenv("FPZ_ARB_BLOCK_BAND_BPS", "200")
    s = Settings()
    assert s.fpz_live_enabled is True
    assert s.fpz_confidence_floor_bps == 8200
    assert s.fpz_arb_block_band_bps == 200.0


# ---------------------------------------------------------------------------
# 10. The DEPLOYED live loop must read the knobs too
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scheduler_agent_gets_the_fpz_knobs(monkeypatch, tmp_path):
    """`scripts/deploy_vps.sh` runs `python -m src.scheduler`, so a flip wired
    only into main.py leaves the live loop on constructor defaults — a silent
    no-op. This pins the scheduler's agent construction to settings.

    Hermetic: the exchange client is stubbed; nothing here opens a socket.
    """
    from src import scheduler as sched
    from src.settings import refresh_settings

    class _Cli:
        async def run(self, *a, **k):  # pragma: no cover - never called
            return {"data": []}

    monkeypatch.setattr(sched, "create_exchange_client", lambda exchange: _Cli())
    monkeypatch.setenv("FPZ_LIVE_ENABLED", "true")
    monkeypatch.setenv("FPZ_SOL_ONLY", "false")
    monkeypatch.setenv("FPZ_BYPASS_ENSEMBLE", "false")
    monkeypatch.setenv("FPZ_CONFIDENCE_FLOOR_BPS", "8200")
    monkeypatch.setenv("FPZ_ARB_PACKAGE_PRIORITY", "true")
    monkeypatch.setenv("FPZ_ARB_BLOCK_BAND_BPS", "200")
    monkeypatch.setenv("EXECUTION_INTENT_DB", str(tmp_path / "execution_intents.sqlite3"))
    refresh_settings()
    try:
        scheduler = sched.TradingScheduler(
            sched.SchedulerConfig(interval_minutes=60, dry_run=True)
        )
        await scheduler.initialize()
        agent = scheduler.agent
        assert agent is not None
        assert agent.fpz_live_enabled is True
        assert agent.fpz_sol_only is False
        assert agent.fpz_bypass_ensemble is False
        assert agent.fpz_confidence_floor_bps == 8200
        assert agent.fpz_arb_package_priority is True
        assert agent.fpz_arb_block_band_bps == 200.0
    finally:
        # Do not leak the cached test settings into other modules' tests.
        for var in (
            "FPZ_LIVE_ENABLED", "FPZ_SOL_ONLY", "FPZ_BYPASS_ENSEMBLE",
            "FPZ_CONFIDENCE_FLOOR_BPS", "FPZ_ARB_PACKAGE_PRIORITY",
            "FPZ_ARB_BLOCK_BAND_BPS",
        ):
            monkeypatch.delenv(var, raising=False)
        refresh_settings()
