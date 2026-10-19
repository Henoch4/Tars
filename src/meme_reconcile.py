"""Live reconciliation — compare local tracked positions to on-chain truth.

Pure comparison only: callers supply the local positions and the observed
on-chain token balances; this module classifies the difference. The network
read (getTokenAccountsByOwner) lives in the script, not here, so the logic is
deterministic and testable and this module never touches the chain or keys.

Chain is the source of truth. Two failure shapes matter in live trading:
  - VANISHED: we think we hold a mint but the wallet shows no balance
    (position closed externally, or a rug burned it) -> alert + pause.
  - ORPHAN: the wallet holds a mint we are not tracking (a fill we missed,
    or a token we bought outside the loop) -> alert + adopt or liquidate.
"""
from __future__ import annotations

_ZERO_EPS = 1e-9


def reconcile(local_positions: dict, onchain_balances: dict,
              min_units: float = _ZERO_EPS) -> dict:
    """Classify local vs on-chain holdings.

    local_positions: {mint: {...}} or {mint: anything} — keys are what we track.
    onchain_balances: {mint: numeric_amount} from the wallet read.
    A balance <= min_units counts as absent (dust/empty account).
    """
    local = set(k for k in (local_positions or {}) if isinstance(k, str) and k)
    chain = {}
    for mint, amt in (onchain_balances or {}).items():
        if not isinstance(mint, str) or not mint:
            continue
        if isinstance(amt, bool) or not isinstance(amt, (int, float)):
            continue
        if float(amt) > min_units:
            chain[mint] = float(amt)

    matched = sorted(local & set(chain))
    vanished = sorted(local - set(chain))
    orphan = sorted(set(chain) - local)
    ok = not vanished and not orphan
    return {
        "ok": ok,
        "counts": {"local": len(local), "onchain": len(chain),
                   "matched": len(matched), "vanished": len(vanished),
                   "orphan": len(orphan)},
        "matched": matched,
        "vanished": vanished,
        "orphan": orphan,
        "orphan_balances": {m: chain[m] for m in orphan},
        "alerts": _alerts(vanished, orphan),
    }


def _alerts(vanished: list, orphan: list) -> list:
    out = []
    for m in vanished:
        out.append({"level": "critical", "kind": "vanished", "mint": m,
                    "detail": "tracked position has no on-chain balance"})
    for m in orphan:
        out.append({"level": "warning", "kind": "orphan", "mint": m,
                    "detail": "on-chain balance not tracked locally"})
    return out


def render_reconciliation(rep: dict) -> str:
    c = rep["counts"]
    lines = [f"=== live reconciliation | {'OK' if rep['ok'] else 'MISMATCH'} ===",
             f"local {c['local']} | on-chain {c['onchain']} | matched {c['matched']} "
             f"| vanished {c['vanished']} | orphan {c['orphan']}"]
    for a in rep["alerts"]:
        lines.append(f"  [{a['level']:8s}] {a['kind']}: {a['mint'][:12]}... — {a['detail']}")
    return "\n".join(lines)
