"""Launch forensics (block-0 cluster + dev-minute-1), measurement only.

Answers two pre-graduation questions free on-chain:
  1. How many distinct buyers bought in the creation slot (block 0)?
     "4 buys packed right behind the dev" is on-chain visible.
  2. Did the creator sell within 60 seconds of creation?

v1 records, never gates: thresholds (4 buys, minute-1) are external claims
awaiting our own calibration. Fail-soft everywhere (LaunchError ->
caller SKIP-defaults fields to None). Bounded: signatures page + up to
MAX_EARLY_TXS parsed transactions per call.
"""
from __future__ import annotations

import logging
import urllib.request
import json as _json

logger = logging.getLogger(__name__)

TIMEOUT_S = 15.0
MAX_EARLY_TXS = 10  # oldest transactions inspected per call
DEV_SELL_WINDOW_S = 60.0  # creator sale within this of creation = minute-1


class LaunchError(RuntimeError):
    """Typed failure: caller records None, never guesses."""


def _default_post(rpc_url: str, timeout_s: float = TIMEOUT_S):
    def post(payload: dict):
        body = _json.dumps(payload).encode()
        req = urllib.request.Request(
            rpc_url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                out = _json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            raise LaunchError(f"transport: {type(e).__name__}: {e}") from e
        if not isinstance(out, dict) or "result" not in out:
            raise LaunchError("transport: malformed response")
        return out["result"]
    return post


class LaunchForensics:
    """One-shot early-history reads (no lived state)."""

    def __init__(self, rpc_url: str | None = None, post=None):
        from .meme_feed import resolve_helius_rpc
        self.rpc_url = rpc_url or resolve_helius_rpc()
        self._post = post or _default_post(self.rpc_url)

    def _call(self, method: str, params: list):
        try:
            return self._post({"jsonrpc": "2.0", "id": 1,
                               "method": method, "params": params})
        except LaunchError:
            raise
        except Exception as e:
            raise LaunchError(f"{method}: {type(e).__name__}: {e}") from e

    def examine(self, mint: str, creator: str | None = None) -> dict:
        """Oldest-signature window analysis. Returns stable keys; unknown ->
        None (never zero-counts as clean)."""
        from .meme_bundle import BundleGraph
        res = self._call("getSignaturesForAddress", [mint, {"limit": 25}])
        if not isinstance(res, list) or not res:
            raise LaunchError(f"no signatures for {mint}")
        sigs = [e.get("signature") for e in res
                if isinstance(e, dict) and not e.get("err")
                and e.get("signature")]
        if not sigs:
            raise LaunchError(f"no usable signatures for {mint}")
        # RPC returns newest-first: the creation window is the tail.
        early = list(reversed(sigs))[:MAX_EARLY_TXS]
        txs = []
        for sig in early:
            tx = self._call("getTransaction",
                            [sig, {"encoding": "jsonParsed",
                                   "maxSupportedTransactionVersion": 0}])
            if isinstance(tx, dict):
                txs.append(tx)
        if not txs:
            raise LaunchError(f"no fetchable early txs for {mint}")
        slots = [t.get("slot", 0) for t in txs]
        creation_slot = min(slots)
        times = [t.get("blockTime") or 0 for t in txs]
        creation_time = min(t for t in times if t) if any(times) else 0
        block0 = [t for t in txs if t.get("slot", 0) == creation_slot]
        buyers: set[str] = set()
        for t in block0:
            keys = ((t.get("transaction", {}).get("message", {})
                     .get("accountKeys", [])) or [])
            for k in keys:
                if isinstance(k, dict) and k.get("signer") and k.get("pubkey"):
                    buyers.add(k["pubkey"])
        if creator:
            buyers.discard(creator)
        dev_sell_s = None
        if creator and creation_time:
            for t in txs:
                bt = t.get("blockTime") or 0
                if not bt or bt - creation_time > DEV_SELL_WINDOW_S:
                    continue
                for ix in BundleGraph._transfer_ixs(t):
                    if ix.get("source") == creator:
                        dt = bt - creation_time
                        if dev_sell_s is None or dt < dev_sell_s:
                            dev_sell_s = float(dt)
        return {
            "mint": mint,
            "creation_slot": creation_slot,
            "creation_time": creation_time or None,
            "block0_txns": len(block0),
            "block0_buyers": len(buyers),
            "dev_sold_minute1": (dev_sell_s is not None
                                 if creator else None),
            "dev_first_sell_s": dev_sell_s,
        }
