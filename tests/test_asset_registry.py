"""W2/S9 regression: one asset registry, every flow iterates it.

Before the fix, the trade universe was declared in three places —
main.py literals, RiskGate defaults, scheduler defaults — plus a probe
list in binance_client. A divergence (add an asset in one place, miss
another) silently changes what the agent can trade or blinds position
reads. These tests pin: the registry is the single source, and every
consumer resolves to exactly it.
"""
from src.assets import (
    SPOT_COMPANIONS,
    TRADE_ASSETS,
    allowed_instruments,
    spot_companions,
    trade_assets,
)


class TestRegistryShape:
    def test_universe_pinned(self):
        assert list(TRADE_ASSETS) == [
            "BTC-USDT-SWAP",
            "ETH-USDT-SWAP",
            "SOL-USDT-SWAP",
            "BNB-USDT-SWAP",
        ]
        assert list(SPOT_COMPANIONS) == [
            "BTC-USDT",
            "ETH-USDT",
            "SOL-USDT",
            "BNB-USDT",
        ]

    def test_no_duplicates_across_lists(self):
        assert len(set(TRADE_ASSETS) | set(SPOT_COMPANIONS)) == (
            len(TRADE_ASSETS) + len(SPOT_COMPANIONS)
        )

    def test_every_swap_has_named_spot_companion(self):
        companions = set(SPOT_COMPANIONS)
        for swap in TRADE_ASSETS:
            assert swap.endswith("-SWAP")
            assert swap.removesuffix("-SWAP") in companions

    def test_accessors_return_copies(self):
        trade_assets().append("FAKE")
        spot_companions().append("FAKE")
        assert "FAKE" not in TRADE_ASSETS
        assert "FAKE" not in SPOT_COMPANIONS

    def test_allowed_instruments_is_union(self):
        assert allowed_instruments() == set(TRADE_ASSETS) | set(SPOT_COMPANIONS)


class TestConsumersResolveToRegistry:
    def test_bare_gate_allows_registry_assets_only(self):
        # D5(a): companions default to EMPTY — a bare gate admits the
        # registry swaps but no spot leg until explicitly declared.
        from src.execution import RiskGate
        gate = RiskGate()
        assert set(gate.allowed_assets) == set(TRADE_ASSETS)
        assert gate.allowed_companions == []
        for inst in TRADE_ASSETS:
            assert gate._is_asset_allowed(inst) is True
        assert gate._is_asset_allowed("BTC-USDT") is False
        assert gate._is_asset_allowed("DOGE-USDT-SWAP") is False
        assert gate._is_asset_allowed("BTC-USD-SWAP") is False

    def test_gate_with_declared_companions_admits_them(self):
        from src.execution import RiskGate
        gate = RiskGate(allowed_companions=spot_companions())
        for inst in allowed_instruments():
            assert gate._is_asset_allowed(inst) is True

    def test_explicit_lists_still_win(self):
        from src.execution import RiskGate
        gate = RiskGate(allowed_assets=["X"], allowed_companions=[])
        assert gate.allowed_assets == ["X"]
        assert gate.allowed_companions == []

    def test_main_module_iterates_registry(self):
        import src.main as main
        assert main._ALLOWED_ASSETS == list(TRADE_ASSETS)
        assert main._ALLOWED_COMPANIONS == list(SPOT_COMPANIONS)

    def test_scheduler_defaults_match_registry(self):
        from src.scheduler import SchedulerConfig, _parse_args
        import sys
        assert list(SchedulerConfig().assets) == list(TRADE_ASSETS)
        # No-args CLI parse also resolves to the registry.
        old_argv = sys.argv
        sys.argv = ["scheduler"]
        try:
            assert _parse_args().assets == list(TRADE_ASSETS)
        finally:
            sys.argv = old_argv

    def test_no_hardcoded_universe_outside_registry(self):
        # The four-swap universe, in order, must appear exactly once in
        # src/ (here) — whitespace-insensitive since the registry formats
        # one per line.
        import pathlib
        import re
        want = re.escape('"BTC-USDT-SWAP", "ETH-USDT-SWAP", '
                         '"SOL-USDT-SWAP", "BNB-USDT-SWAP"')
        src = pathlib.Path("src")
        hits = [p for p in src.rglob("*.py")
                if re.search(want, re.sub(r"\s+", " ", p.read_text()))]
        assert hits == [src / "assets.py"], f"universe duplicated in: {hits}"
