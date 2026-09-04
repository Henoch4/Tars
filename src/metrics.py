"""Structured metrics surface (S3) + liveness heartbeat (S2).

Replaces log-grep observability for anything a test or alert asserts on:
counters for transitions, gauges for persistent state, and a per-cycle
heartbeat record distinguishing "no cycle ran" from "ran, no trades" from
"rejected by risk gate".

Ika naming rules, enforced:
- counters end `_total`; gauges are plain nouns (asserted, not conventional).
- Transitions fire the counter; persistent state lives in a paired gauge —
  a condition that persists across ticks must not re-increment per tick.
- Label sets are CLOSED: method, code, direction, state, reason, outcome.
  Session ids, wallet addresses, asset names, and payload digests must
  never become label values (unbounded series).

Process-local and dependency-free (stdlib only) so the risk gate and the
exchange wrapper can import it without cycles. For cross-process watching,
the scheduler persists the heartbeat to LOOP_HEARTBEAT_PATH.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
_gauges: dict[str, float] = {}
_heartbeat: dict | None = None

# S2 outcome vocabulary (closed): what one cycle did.
OUTCOME_TRADED = "traded"        # >=1 execution
OUTCOME_NO_TRADES = "no_trades"  # ran, zero decisions
OUTCOME_REJECTED = "rejected"    # decisions but zero executions
OUTCOME_ERROR = "error"          # cycle raised before completing


def inc(name: str, labels: dict[str, str] | None = None, amount: int = 1) -> int:
    """Increment a transition counter. Returns the new count."""
    assert name.endswith("_total"), f"counter {name!r} must end in _total (S3)"
    key = (name, tuple(sorted((labels or {}).items())))
    with _lock:
        _counters[key] = _counters.get(key, 0) + amount
        return _counters[key]


def set_gauge(name: str, value: float) -> None:
    """Set a persistent-state gauge (idempotent — safe to call per tick)."""
    assert not name.endswith("_total"), f"gauge {name!r} must not end in _total (S3)"
    with _lock:
        _gauges[name] = float(value)


def get_gauge(name: str) -> float | None:
    with _lock:
        return _gauges.get(name)


def beat(cycle_id: str, outcome: str, counts: dict | None = None,
         errors: int = 0) -> dict:
    """Record one completed cycle heartbeat (S2). Returns the record."""
    assert outcome in (OUTCOME_TRADED, OUTCOME_NO_TRADES, OUTCOME_REJECTED,
                       OUTCOME_ERROR), f"unknown outcome {outcome!r}"
    record = {
        "cycle_id": cycle_id,
        "outcome": outcome,
        "completed_at": time.time(),
        "counts": dict(counts or {}),
        "errors": int(errors),
    }
    with _lock:
        global _heartbeat
        _heartbeat = record
    return record


def get_heartbeat() -> dict | None:
    with _lock:
        return dict(_heartbeat) if _heartbeat else None


def ml_degradations_bridged() -> dict[str, int]:
    """Fold signals.ML_DEGRADATIONS into closed reason labels.

    Degradation keys are "ASSET:reason" — the asset prefix is stripped
    (assets must never become label values); counts for the same reason
    across assets are summed.
    """
    try:
        from .signals import ML_DEGRADATIONS
        items = list(ML_DEGRADATIONS.items())
    except Exception:
        return {}
    bridged: dict[str, int] = {}
    for key, count in items:
        reason = key.split(":", 1)[1] if ":" in key else key
        bridged[reason] = bridged.get(reason, 0) + int(count)
    return bridged


def snapshot() -> dict:
    """Full JSON-serializable snapshot for /api/v1/metrics and tests."""
    with _lock:
        counters = {
            name + ("{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"
                   if labels else ""): count
            for (name, labels), count in sorted(_counters.items())
        }
        gauges = dict(_gauges)
        heartbeat = dict(_heartbeat) if _heartbeat else None
    return {
        "counters": counters,
        "gauges": gauges,
        "heartbeat": heartbeat,
        "ml_degradations": ml_degradations_bridged(),
    }


def reset() -> None:
    """Test seam: clear all counters, gauges, and heartbeat."""
    with _lock:
        _counters.clear()
        _gauges.clear()
        global _heartbeat
        _heartbeat = None
