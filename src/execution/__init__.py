"""
Public API for the execution layer. Everything that lived in execution.py is
re-exported here so existing importers (``from src.execution import RiskGate``)
keep working unchanged after the Block G modular split.
"""
from __future__ import annotations

from .models import (
    OrderSide,
    OrderType,
    TimeInForce,
    OrderStatus,
    OrderRequest,
    OrderResult,
    ExecutionError,
    RISK_DEFAULTS,
    slippage_fraction_to_pct,
    slippage_pct_to_fraction,
)
from .risk_gate import (
    DBCCurveConfig,
    DBCCurveType,
    DurableDailyCounters,
    RiskCheckResult,
    RiskGate,
    SCORING_PROFILE_VERSION,
    SCORING_WEIGHTS,
    validate_scoring_weights,
)
from .executor import OrderExecutor

__all__ = [
    "OrderSide",
    "OrderType",
    "TimeInForce",
    "OrderStatus",
    "OrderRequest",
    "OrderResult",
    "ExecutionError",
    "DBCCurveConfig",
    "DBCCurveType",
    "DurableDailyCounters",
    "RiskCheckResult",
    "RiskGate",
    "RISK_DEFAULTS",
    "SCORING_PROFILE_VERSION",
    "SCORING_WEIGHTS",
    "slippage_fraction_to_pct",
    "slippage_pct_to_fraction",
    "validate_scoring_weights",
    "OrderExecutor",
]
