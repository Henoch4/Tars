"""Sanitized product capability map for operator and public status surfaces."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ProductState = Literal["built", "paper_shadow", "gated", "integration", "planned"]


@dataclass(frozen=True)
class ProductArea:
    asset_class: str
    strategy: str
    venue_or_chain: str
    state: ProductState
    meaning: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def product_status_map() -> list[ProductArea]:
    """Return the current multi-asset map without credentials or live controls."""
    return [
        ProductArea("Crypto majors", "Carry / funding", "OKX / Binance research adapters", "built", "Paper lifecycle and risk accounting"),
        ProductArea("Memecoins", "Core satellite + age variant", "Solana", "paper_shadow", "Evidence collection and matched experiments"),
        ProductArea("Utility tokens", "Safety / sellability research", "Solana", "gated", "Provider and chain evidence still required"),
        ProductArea("Equities", "Research backlog", "Planned venue", "planned", "Not an active trading surface"),
        ProductArea("Audit / vault rails", "Decision evidence", "X Layer testnet", "built", "Audit and reconciliation infrastructure"),
        ProductArea("Perpetuals", "Market-data integration", "Injective testnet", "integration", "Connectivity and fixture validation"),
    ]


def product_status_payload() -> dict[str, object]:
    """Return a stable, read-only payload suitable for the dashboard API."""
    return {
        "mode": "paper_shadow_only",
        "live_execution_enabled": False,
        "gta_scope": "historical_analytics_only",
        "areas": [area.to_dict() for area in product_status_map()],
    }
