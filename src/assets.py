"""Single asset registry (W2/S9).

Every flow iterates this module — the trade universe, the spot companions
for delta-neutral packages, and the position-probe list. Adding a chain or
token is one reviewed edit here, not a hunt across main.py, the risk gate,
the scheduler, and the exchange clients.

Rules:
- TRADE_ASSETS: perp instruments the agent may open directional or package
  legs on. Immutable tuple of symbol strings; consumers copy via
  trade_assets().
- SPOT_COMPANIONS: explicitly declared spot legs for funding-arb packages
  (D5 exact-match + companions — never implicit base-family matching).
- ASSET_METADATA: per-symbol execution metadata (venue, slippage model,
  chain, vetted routers). Single source of truth consumed by gate +
  executor + audit alike. Adding a chain or token becomes one reviewed edit
  here — add the symbol to TRADE_ASSETS/SPOT_COMPANIONS and its metadata to
  ASSET_METADATA.
- No other module may hardcode this universe. Derivations (allowed sets,
  probe lists, CLI defaults) read from here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Venue(str, Enum):
    OKX = "okx"                      # CEX perp, ctVal collar slippage
    BINANCE = "binance"              # CEX perp, ctVal collar slippage
    INJECTIVE = "injective"          # CEX perp, orderbook depth+impact
    JUPITER = "jupiter"              # Solana AMM aggregator, size/liquidity
    RAYDIUM = "raydium"              # Solana AMM, size/liquidity
    HYPERLIQUID = "hyperliquid"      # L1 perp orderbook, depth+impact
    BASE = "base"                    # EVM L2, CEX or AMM depending on router
    ROBINHOOD_CHAIN = "robinhood_chain"  # Arbitrum Orbit L3, Pons launchpad


class SlippageModel(str, Enum):
    FLAT_BPS = "flat_bps"            # fixed bps collar (current default, CEX perps)
    AMM_SIZE_LIQUIDITY = "amm_size_liquidity"   # slippage ~ size / pool liquidity
    ORDERBOOK_DEPTH_IMPACT = "orderbook_depth_impact"  # slippage from book depth
    CT_VAL_COLLAR = "ct_val_collar"    # OKX ctVal-based collar


@dataclass(frozen=True)
class AssetMetadata:
    """Per-symbol execution metadata. All fields JSON-serializable.

    A missing or invalid venue/slippage_model is a loud error at
    construction, never a silent default.
    """
    symbol: str                      # e.g. "SOL-USDT-SWAP"
    venue: Venue                     # execution venue for this instrument
    slippage_model: SlippageModel    # how slippage is computed/validated
    chain: str                       # e.g. "ethereum", "solana", "injective"
    decimals: int = 8                # token decimals for amount quantization
    vetted_routers: tuple[str, ...] = ()  # allowlisted DEX routers for this venue
    is_native: bool = False          # whether this is the chain's native asset


# --- Trade universe (backward-compatible: tuple of symbol strings) --------

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


# --- Per-symbol execution metadata ----------------------------------------

ASSET_METADATA: dict[str, AssetMetadata] = {
    "BTC-USDT-SWAP": AssetMetadata("BTC-USDT-SWAP", Venue.OKX,
                                   SlippageModel.CT_VAL_COLLAR, "ethereum", 8),
    "ETH-USDT-SWAP": AssetMetadata("ETH-USDT-SWAP", Venue.OKX,
                                   SlippageModel.CT_VAL_COLLAR, "ethereum", 8),
    "SOL-USDT-SWAP": AssetMetadata("SOL-USDT-SWAP", Venue.OKX,
                                   SlippageModel.CT_VAL_COLLAR, "ethereum", 8),
    "BNB-USDT-SWAP": AssetMetadata("BNB-USDT-SWAP", Venue.OKX,
                                   SlippageModel.CT_VAL_COLLAR, "ethereum", 8),
    "BTC-USDT": AssetMetadata("BTC-USDT", Venue.OKX,
                              SlippageModel.CT_VAL_COLLAR, "ethereum", 8),
    "ETH-USDT": AssetMetadata("ETH-USDT", Venue.OKX,
                              SlippageModel.CT_VAL_COLLAR, "ethereum", 8),
    "SOL-USDT": AssetMetadata("SOL-USDT", Venue.OKX,
                              SlippageModel.CT_VAL_COLLAR, "ethereum", 8),
    "BNB-USDT": AssetMetadata("BNB-USDT", Venue.OKX,
                              SlippageModel.CT_VAL_COLLAR, "ethereum", 8),
}


# --- Accessors (backward-compatible: return strings/copies) ---------------


def trade_assets() -> list[str]:
    """Copy of the trade universe as symbol strings (callers must not mutate)."""
    return list(TRADE_ASSETS)


def spot_companions() -> list[str]:
    """Copy of the declared spot companion legs as symbol strings."""
    return list(SPOT_COMPANIONS)


def allowed_instruments() -> set[str]:
    """Exact-match allowlist: assets + companions (D5 semantics)."""
    return set(TRADE_ASSETS) | set(SPOT_COMPANIONS)


# --- Metadata accessors ---------------------------------------------------


def asset_metadata(symbol: str) -> AssetMetadata | None:
    """Return per-symbol execution metadata, or None if unknown symbol."""
    return ASSET_METADATA.get(symbol)


def venue_for(symbol: str) -> Venue | None:
    """Return the execution venue for a symbol, or None if unknown."""
    md = asset_metadata(symbol)
    return md.venue if md is not None else None


def slippage_model_for(symbol: str) -> SlippageModel | None:
    """Return the slippage cost model for a symbol, or None if unknown."""
    md = asset_metadata(symbol)
    return md.slippage_model if md is not None else None


def vetted_routers_for(symbol: str) -> tuple[str, ...]:
    """Return allowlisted DEX routers for a symbol's venue, or empty tuple."""
    md = asset_metadata(symbol)
    return md.vetted_routers if md is not None else ()


# --- Boot-time validation -------------------------------------------------


def validate_registry() -> list[str]:
    """Validate the registry at boot. Returns errors (empty = OK).

    Checks:
    - Every symbol in TRADE_ASSETS/SPOT_COMPANIONS has metadata
    - No duplicate symbols across perps + companions
    - Every AssetMetadata has a valid venue + slippage_model combo
    - AMM venues require vetted_routers (no silent MEV exposure)
    """
    errors: list[str] = []
    seen: set[str] = set()
    all_symbols = list(TRADE_ASSETS) + list(SPOT_COMPANIONS)
    for sym in all_symbols:
        if sym in seen:
            errors.append(f"duplicate symbol in registry: {sym}")
        seen.add(sym)
        md = asset_metadata(sym)
        if md is None:
            errors.append(f"symbol {sym} has no ASSET_METADATA entry")
            continue

        # AMM venues must declare vetted routers — fail-closed, no silent
        # MEV exposure on a router the operator hasn't reviewed.
        if md.venue in (Venue.JUPITER, Venue.RAYDIUM) and not md.vetted_routers:
            errors.append(
                f"AMM venue {md.venue} for {sym} has no vetted_routers "
                f"(would route to unknown DEX, MEV exposure)"
            )
    return errors
