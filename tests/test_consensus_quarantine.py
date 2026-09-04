"""Swarm quarantine regression (resurrected ika BFT drop): fault detection,
not network consensus. A trader voting against the cohort majority, emitting
NaN confidences, or lock-stepping another voter gets down-weighted (never
excluded); sustained agreement releases. All-NEUTRAL rounds are agreement,
not pathology.
"""
from src.consensus import ConsensusGate
from src.signals import Signal
from src.trader import MarketContext, TraderStrategy


def _ctx():
    return MarketContext(asset="BTC-USDT-SWAP", prices=[100.0] * 30,
                         price_data=[])


class _Scripted(TraderStrategy):
    """Returns canned (direction, confidence) per observation index."""

    def __init__(self, name, script, weight=1.0, cohort="majors"):
        super().__init__(name=name, asset_class_cohort=cohort, weight=weight)
        self.script = list(script)
        self.calls = 0

    def on_data(self, context):
        direction, conf = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return Signal(strategy=self.name, asset=context.asset,
                      direction=direction, confidence_bps=conf,
                      rationale="scripted")


def _majors(*traders):
    return [t for t in traders]


class TestNaNSanitized:
    def test_nan_confidence_cannot_poison_cohort(self):
        gate = ConsensusGate(divergence_window=100)  # quarantine off the table
        good = _Scripted("good", [("LONG", 8000)] * 3)
        bad = _Scripted("bad", [("LONG", float("nan"))] * 3)
        for _ in range(3):
            res = gate.compute_consensus("BTC-USDT-SWAP", "majors",
                                         _majors(good, bad), _ctx())
        assert res.total_weight == res.total_weight  # not NaN
        assert res.consensus_reached is True
        assert res.direction == "LONG"


class TestDivergence:
    def test_persistent_contrarian_quarantined(self):
        gate = ConsensusGate(divergence_window=3, quarantine_factor=0.25)
        # Distinct confidences: real signals are continuous; byte-identical
        # votes would (correctly) trip the lock-step rule instead.
        majority = [_Scripted(f"m{i}", [("LONG", 8000 + i * 100)] * 6)
                    for i in range(3)]
        rogue = _Scripted("rogue", [("SHORT", 7500)] * 6)
        res = None
        for _ in range(6):
            res = gate.compute_consensus("BTC-USDT-SWAP", "majors",
                                         _majors(*majority, rogue), _ctx())
        assert "rogue" in res.quarantined
        rogue_vote = next(v for v in res.votes if v.trader_name == "rogue")
        assert rogue_vote.quarantined is True
        assert rogue_vote.effective_weight == 0.25
        assert rogue_vote.vote_weight == 0.25 * 0.75  # rogue conf 7500

    def test_transient_dissent_never_quarantined(self):
        gate = ConsensusGate(divergence_window=3)
        majority = [_Scripted(f"m{i}", [("LONG", 8000 + i * 100)] * 4)
                    for i in range(3)]
        # Dissent twice, then rejoin — streak resets, no quarantine.
        flip = _Scripted("flip", [("SHORT", 8000), ("SHORT", 8000),
                                  ("LONG", 8000), ("LONG", 8000)])
        for _ in range(4):
            res = gate.compute_consensus("BTC-USDT-SWAP", "majors",
                                         _majors(*majority, flip), _ctx())
        assert res.quarantined == []

    def test_release_after_agreement_streak(self):
        gate = ConsensusGate(divergence_window=2, release_window=3)
        majority = [_Scripted(f"m{i}", [("LONG", 8000 + i * 100)] * 8)
                    for i in range(3)]
        # Rejoins with its own confidence (7900, not m0's 8000) so the
        # release assertion tests divergence-recovery, not lock-step.
        rogue = _Scripted("rogue", [("SHORT", 8000)] * 2 + [("LONG", 7900)] * 6)
        res = None
        for _ in range(8):
            res = gate.compute_consensus("BTC-USDT-SWAP", "majors",
                                         _majors(*majority, rogue), _ctx())
            if res.quarantined:
                break
        assert "rogue" in res.quarantined
        for _ in range(6):
            res = gate.compute_consensus("BTC-USDT-SWAP", "majors",
                                         _majors(*majority, rogue), _ctx())
        assert "rogue" not in res.quarantined

    def test_all_neutral_is_agreement(self):
        gate = ConsensusGate(divergence_window=2)
        traders = [_Scripted(f"t{i}", [("NEUTRAL", 3000)] * 4) for i in range(3)]
        for _ in range(4):
            res = gate.compute_consensus("BTC-USDT-SWAP", "majors",
                                         _majors(*traders), _ctx())
        assert res.quarantined == []
        assert res.direction == "NEUTRAL"

    def test_disabled_gate_never_quarantines(self):
        gate = ConsensusGate(divergence_window=1, enable_quarantine=False)
        majority = [_Scripted(f"m{i}", [("LONG", 8000 + i * 100)] * 3)
                    for i in range(3)]
        rogue = _Scripted("rogue", [("SHORT", 8000)] * 3)
        for _ in range(3):
            res = gate.compute_consensus("BTC-USDT-SWAP", "majors",
                                         _majors(*majority, rogue), _ctx())
        assert res.quarantined == []
        rogue_vote = next(v for v in res.votes if v.trader_name == "rogue")
        assert rogue_vote.effective_weight == 1.0


class TestLockstep:
    def test_copy_quarantines_lower_weight(self):
        gate = ConsensusGate(divergence_window=3)
        orig = _Scripted("orig", [("LONG", 8000)] * 5, weight=1.0)
        copy = _Scripted("copy", [("LONG", 8000)] * 5, weight=0.5)
        third = _Scripted("third", [("SHORT", 8000)] * 5, weight=1.0)
        for _ in range(5):
            res = gate.compute_consensus("BTC-USDT-SWAP", "majors",
                                         _majors(orig, copy, third), _ctx())
        assert "copy" in res.quarantined
        assert "orig" not in res.quarantined

    def test_independent_votes_never_flagged(self):
        gate = ConsensusGate(divergence_window=2)
        a = _Scripted("a", [("LONG", 8000), ("SHORT", 7000)] * 3, weight=1.0)
        b = _Scripted("b", [("LONG", 7000), ("SHORT", 8000)] * 3, weight=1.0)
        for _ in range(6):
            res = gate.compute_consensus("BTC-USDT-SWAP", "majors",
                                         _majors(a, b), _ctx())
        assert res.quarantined == []
