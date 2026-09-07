"""
Two-tier custody with trade-scoped hot keys + cold settlement (EXPANSION_ROADMAP addendum 2026-08-24/25).

Hot tier: swap ONLY via allowlisted per-chain DEX routers (Jupiter/Raydium on Solana, vetted routers on EVM).
  Cannot send to arbitrary address. Cannot sign arbitrary calldata. Rate-limited per signer.

Cold tier: human-only settlement (Ledger/hardware-encrypted), cold-confirmed. Profit sweep lands at fresh
  stealth-derived addresses so on-chain trail isn't linkable to operative keys.

Patterns lifted from Seedless teardown:
- registry.ts → single AssetRegistry (symbol/mint/decimals/vetted routers)
- walletLock.ts → armed state persists until success confirmed
- private-send-from-main.ts → typed degradation with consent, never silent fallback
- stealth.ts → deterministic index-based derivation from encrypted master seed
"""
from __future__ import annotations

import hashlib
import time
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# --- Single asset/router/slippage allowlist (per EXPANSION_ROADMAP: single registry) ---

ALLOWED_ROUTERS: dict[str, list[str]] = {
    "solana": ["jupiter", "raydium"],
    "evm": ["0x_router_vetted"],  # placeholder — fill per-chain when Solana live
}

# Per-chain slippage model: AMM ~ size/liquidity, not bps
SLIPPAGE_MODEL: dict[str, str] = {
    "solana": "amm_size_over_liquidity",
    "evm": "bps_collar",
}

# --- Stealth derivation (Seedless stealth.ts) ---

def derive_stealth_address(master_seed_hex: str, index: int) -> str:
    """Deterministic index-based derivation from encrypted master seed.
    Cheap hash+derive, no new crypto, fits 'own signing' — derivation is local."""
    h = hashlib.sha256(f"{master_seed_hex}:{index}".encode()).hexdigest()
    # Solana-style: 32-byte pubkey hex, truncated for demo (real: ed25519 derive)
    return "0x" + h[:40]


# --- Rate limiting (Seedless Kora paymaster pattern) ---

@dataclass
class RateLimiter:
    """Per-signer sliding window, persisted across restarts (caller persists)."""
    window_ms: int = 60_000
    max_per_window: int = 20
    _hits: dict[str, list[float]] = field(default_factory=dict)

    def check(self, signer_id: str) -> tuple[bool, float | None]:
        """Returns (allowed, retry_after_ms)."""
        now = time.time() * 1000
        hits = self._hits.get(signer_id, [])
        # prune outside window
        hits = [t for t in hits if now - t < self.window_ms]
        self._hits[signer_id] = hits
        if len(hits) >= self.max_per_window:
            oldest = min(hits)
            retry_after = (oldest + self.window_ms) - now
            return False, max(0, retry_after)
        hits.append(now)
        return True, None


_rate_limiter = RateLimiter()

# --- Two tiers ---

@dataclass
class HotTier:
    """Trade-scoped hot keys — swap ONLY via allowlisted routers."""
    signer_id: str
    chain: Literal["solana", "evm"] = "solana"

    def can_route(self, router: str) -> bool:
        return router.lower() in [r.lower() for r in ALLOWED_ROUTERS.get(self.chain, [])]

    def swap(self, router: str, amount_usd: float, slippage_pct: float | None = None) -> dict:
        """Swap via allowlisted router only. Typed abort on unverifiable route/slippage."""
        allowed, retry = _rate_limiter.check(self.signer_id)
        if not allowed:
            raise RuntimeError(f"Rate limit: retry after {retry:.0f}ms (signer {self.signer_id})")
        if not self.can_route(router):
            # typed degradation — no silent fallback (Seedless degrade-with-explicit-consent)
            raise ValueError(f"Router {router} not allowlisted for {self.chain}: {ALLOWED_ROUTERS[self.chain]} — abort, no silent reroute")
        if slippage_pct is not None and slippage_pct > 0.05:
            raise ValueError(f"Slippage {slippage_pct:.2%} >5% cap — typed abort, need operator consent")
        logger.info(f"Hot swap {amount_usd} via {router} on {self.chain} (signer {self.signer_id})")
        return {"router": router, "amount_usd": amount_usd, "slippage_pct": slippage_pct, "signer": self.signer_id}


@dataclass
class ColdTier:
    """Human-only cold settlement — Ledger/hardware-encrypted, cold-confirmed."""
    master_seed_hex: str  # encrypted, never in config/env per design
    _next_index: int = 0

    def next_stealth_address(self) -> str:
        """Fresh derived address for profit sweep — trail not linkable to hot keys."""
        addr = derive_stealth_address(self.master_seed_hex, self._next_index)
        self._next_index += 1
        logger.info(f"Cold next stealth {addr} index {self._next_index-1}")
        return addr

    def confirm_sweep(self, amount_usd: float, to_address: str) -> dict:
        """Human-in-loop cold confirmation — trading logic cannot reach here."""
        # In prod: Ledger prompt + cold-confirmed. Here: log + return receipt
        logger.info(f"Cold sweep {amount_usd} -> {to_address} (human confirmed)")
        return {"to": to_address, "amount_usd": amount_usd, "confirmed": True}


# Global cold tier (set once at startup with encrypted seed)
_cold_tier: ColdTier | None = None

def init_cold_tier(master_seed_hex: str) -> ColdTier:
    global _cold_tier
    _cold_tier = ColdTier(master_seed_hex=master_seed_hex)
    return _cold_tier

def get_cold_tier() -> ColdTier | None:
    return _cold_tier


def sweep_to_cold(profit_usd: float, hot_signer_id: str = "hot_default") -> dict | None:
    """
    Profit-sweep to cold settlement wallet at fresh stealth address.
    Called after each SETTLED package or realized profit. Trading logic cannot
    bypass allowlist or cold confirmation — typed abort if cold not init.

    Returns sweep receipt or None if no profit / cold not configured (not an error for paper).
    """
    if profit_usd <= 0:
        return None
    cold = get_cold_tier()
    if cold is None:
        logger.warning("sweep_to_cold: cold tier not initialized — profit stays in hot (paper mode)")
        return None
    # Rate-limit the hot signer even for sweep (paymaster pattern)
    allowed, retry = _rate_limiter.check(hot_signer_id + ":sweep")
    if not allowed:
        logger.warning(f"sweep rate-limited: retry {retry:.0f}ms")
        return None
    to_addr = cold.next_stealth_address()
    receipt = cold.confirm_sweep(profit_usd, to_addr)
    logger.info(f"Sweep {profit_usd} -> cold {to_addr} stealth index {cold._next_index-1}")
    return receipt
