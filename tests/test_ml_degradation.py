"""S5/W3 regression: ML-carry failures block with typed, counted reasons.

Before the fix, a failed tars-lora inference silently fell back to a
rule-based LONG under the `ml_funding_carry` strategy name (S5: a default
landing inside the value domain), and the package gate fell through to the
fixed threshold the operator explicitly replaced (W3: silent reroute).
Now: no ML prediction -> NEUTRAL + degraded=True + degradation_reason,
every skip counted in ML_DEGRADATIONS, and the gate blocks instead of
falling through.
"""
import pytest

import src.ml_inference as ml_inference
import src.signals as signals
from src.signals import ML_DEGRADATIONS, ml_funding_carry_signal


@pytest.fixture(autouse=True)
def clean_degradations():
    ML_DEGRADATIONS.clear()
    yield
    ML_DEGRADATIONS.clear()


def _passing_market():
    # basis 10bps (>= 5), funding 0.5% per period (annualized huge) -> rule passes
    return dict(
        asset="BTC-USDT-SWAP",
        spot_price=100.0,
        perp_price=100.1,
        funding_rate=0.005,
        funding_history=[0.004, 0.005, 0.006],
        price_history=[99.0, 99.5, 100.0, 100.1],
    )


class _Decision:
    def __init__(self, will_clear, confidence=0.9, raw_answer="yes"):
        self.will_clear = will_clear
        self.confidence = confidence
        self.raw_answer = raw_answer


def test_ml_failure_never_longs_even_when_rule_passes(monkeypatch):
    """The S5 core: ML down + rule gate passing must NOT emit LONG."""
    def boom(features):
        raise RuntimeError("weights missing")

    monkeypatch.setattr(ml_inference, "predict_carry_clear", boom)
    # signals.py imports the names inside the function from ml_inference,
    # so patching the source module takes effect.
    sig = ml_funding_carry_signal(**_passing_market())
    assert sig.strategy == "ml_funding_carry"
    assert sig.direction == "NEUTRAL"
    assert sig.metadata["degraded"] is True
    assert sig.metadata["degradation_reason"] == "ml_unavailable:RuntimeError"
    assert sig.metadata["rule_passes"] is True  # reported, not acted on
    assert sum(ML_DEGRADATIONS.values()) == 1


def test_ml_failure_reason_names_error_class(monkeypatch):
    def boom(features):
        raise ConnectionError("cuda gone")

    monkeypatch.setattr(ml_inference, "predict_carry_clear", boom)
    sig = ml_funding_carry_signal(**_passing_market())
    assert sig.metadata["degradation_reason"] == "ml_unavailable:ConnectionError"
    assert any("ConnectionError" in k for k in ML_DEGRADATIONS)


def test_ml_success_path_unchanged(monkeypatch):
    """Full-fidelity path keeps exact behavior: ML YES + rule pass -> LONG,
    degraded=False."""
    monkeypatch.setattr(
        ml_inference, "predict_carry_clear",
        lambda features: _Decision(True, 0.9, "yes"),
    )
    sig = ml_funding_carry_signal(**_passing_market())
    assert sig.direction == "LONG"
    assert sig.metadata["degraded"] is False
    assert sig.metadata["degradation_reason"] is None
    assert ML_DEGRADATIONS == {}


def test_ml_no_plus_rule_pass_stays_neutral(monkeypatch):
    """ML says NO while rules pass -> NEUTRAL, not degraded (a real
    prediction happened), confidence stays low."""
    monkeypatch.setattr(
        ml_inference, "predict_carry_clear",
        lambda features: _Decision(False, 0.9, "no"),
    )
    sig = ml_funding_carry_signal(**_passing_market())
    assert sig.direction == "NEUTRAL"
    assert sig.metadata["degraded"] is False


def _gate_agent(**overrides):
    from src.agent import AutonomousTradingAgent
    from src.execution import RiskGate
    from src.multi_leg import MultiLegExecutionManager

    class _StubCli:
        async def run(self, *args, **kwargs):
            return {"data": []}

    gate = RiskGate(
        max_position_usd=5000,
        allowed_assets=["BTC-USDT-SWAP"],
        allowed_companions=["BTC-USDT"],
    )
    kwargs = dict(
        okx_cli=_StubCli(),
        risk_gate=gate,
        dry_run=True,
        use_ml_carry_gate=True,
        multi_leg_manager=MultiLegExecutionManager(),
    )
    kwargs.update(overrides)
    return AutonomousTradingAgent(**kwargs)


def test_gate_blocks_when_client_missing():
    """ML requested but client unavailable -> BLOCKED, counted. The fixed
    threshold (0.5% funding would clear it) must not run as fallback."""
    agent = _gate_agent()
    agent._ml_carry_client = None  # simulate init failure at runtime
    md = {"funding_rate": 0.005}  # would clear funding_arb_min_rate=0.001
    assert agent._funding_arb_opportunity("BTC-USDT-SWAP", md, 100.0, 100.1) is False
    assert any("ml_client" in k for k in ML_DEGRADATIONS)


def test_gate_blocks_when_prices_missing():
    """ML on but no spot/perp price -> BLOCKED, counted, no threshold fall-through."""
    agent = _gate_agent()
    assert agent._ml_carry_client is not None
    md = {"funding_rate": 0.005}
    assert agent._funding_arb_opportunity("BTC-USDT-SWAP", md, None, None) is False
    assert any("ml_missing_prices" in k for k in ML_DEGRADATIONS)


def test_gate_blocks_degraded_signal(monkeypatch):
    """A degraded (NEUTRAL) ML signal blocks explicitly with its reason."""
    monkeypatch.setattr(
        ml_inference, "predict_carry_clear",
        lambda features: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    agent = _gate_agent()
    md = {"funding_rate": 0.005}
    assert agent._funding_arb_opportunity("BTC-USDT-SWAP", md, 100.0, 100.1) is False
    assert any("ml_unavailable" in k for k in ML_DEGRADATIONS)


def test_gate_approves_on_ml_yes(monkeypatch):
    """Approval path unchanged: ML YES + threshold cleared -> True."""
    monkeypatch.setattr(
        ml_inference, "predict_carry_clear",
        lambda features: _Decision(True, 0.9, "yes"),
    )
    agent = _gate_agent()
    md = {"funding_rate": 0.005}
    assert agent._funding_arb_opportunity("BTC-USDT-SWAP", md, 100.0, 100.1) is True
    assert ML_DEGRADATIONS == {}
