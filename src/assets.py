"""Single asset registry (W2/S9).

Every flow iterates this module — the trade universe, the spot companions
for delta-neutral packages, and the position-probe list. Adding a chain or
token is one reviewed edit here, not a hunt across main.py, the risk gate,
the scheduler, and the exchange clients.

Rules:
- TRADE_ASSETS: perp instruments the agent may open directional or package
  legs on. Immutable tuple; consumers copy via trade_assets().
- SPOT_COMPANIONS: explicitly declared spot legs for funding-arb packages
  (D5 exact-match + companions — never implicit base-family matching).
- No other module may hardcode this universe. Derivations (allowed sets,
  probe lists, CLI defaults) read from here.
"""
from __future__ import annotations


TRADE_ASSETS: tuple[str, ...] = (
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "BNB-USDT-SWAP",
)

SPOT_COMPANIONS: tuple[str, ...] = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
)


def trade_assets() -> list[str]:
    """Copy of the trade universe (callers must not mutate the registry)."""
    return list(TRADE_ASSETS)


def spot_companions() -> list[str]:
    """Copy of the declared spot companion legs."""
    return list(SPOT_COMPANIONS)


def allowed_instruments() -> set[str]:
    """Exact-match allowlist: assets + companions (D5 semantics)."""
    return set(TRADE_ASSETS) | set(SPOT_COMPANIONS)
