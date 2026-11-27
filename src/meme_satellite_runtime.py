"""Composition root for the main memecoin satellite control plane."""
from __future__ import annotations

from typing import Any, Callable

from .meme_exit_adapter import MainSatelliteExitAdapter
from .meme_live_adapter import SinglePositionReconciler
from .meme_supervisor import SatelliteSupervisor, SupervisorConfig


def build_single_position_supervisor(
    config: SupervisorConfig,
    *,
    screen_fn: Callable[[], list],
    position_loader: Callable[[], Any | None],
    position_saver: Callable[[Any], None],
    balance_reader: Callable[[str, str], Any] | None,
    owner: str,
    sell_fn: Callable[..., Any],
    entry_fn: Callable[[Any], dict] | None = None,
    exit_confirm: str = "",
    flatten_confirm: str = "",
) -> SatelliteSupervisor:
    """Wire screening, reconciliation, exits, and flattening.

    Empty confirmations intentionally keep the composed runtime paper-only.
    """
    reconciler = SinglePositionReconciler(position_loader, balance_reader, owner)
    exits = MainSatelliteExitAdapter(
        position_loader, position_saver, balance_reader, owner, sell_fn
    )

    def observe_exits():
        return exits.exit(confirm=exit_confirm).__dict__

    def emergency_flatten():
        return exits.flatten(confirm=flatten_confirm)

    return SatelliteSupervisor(
        config,
        screen_fn=screen_fn,
        entry_fn=entry_fn,
        exit_fn=observe_exits,
        reconcile_fn=reconciler,
        flatten_fn=emergency_flatten,
    )
