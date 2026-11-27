"""Small runtime contract used by deployment smoke checks.

The runtime deliberately contains no broker, wallet, or network side effects;
service-specific loops own those concerns and must opt in explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

@dataclass(frozen=True)
class RuntimePolicy:
    dry_run: bool = True
    allow_live: bool = False

    @classmethod
    def from_env(cls) -> "RuntimePolicy":
        dry_run = os.getenv("DRY_RUN", "true").strip().lower() == "true"
        allow_live = os.getenv("ALLOW_LIVE", "false").strip().lower() == "true"
        if allow_live and dry_run:
            raise ValueError("ALLOW_LIVE=true conflicts with DRY_RUN=true")
        return cls(dry_run=dry_run, allow_live=allow_live)

def import_only() -> bool:
    """Deployment smoke helper: importing runtime never starts work."""
    return True
