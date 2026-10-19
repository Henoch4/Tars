"""Funding-graph / bundle analysis, lightweight (Ask-2 conditional GO).

Question: do the top holders look independent but share funding ancestors?
Top-N holder addresses -> one funding hop each (SOL transfers in) ->
shared direct funders form clusters. v1 is ONE hop by design: the dominant
industrial pattern is a distributor funding many buyers directly; deeper
walks multiply cost and CEX-hot-wallet noise. A second hop is deferred
until measured data demands it.

Bounds for the free tier: top_n=15 holders, few signatures each, capped tx
fetches, fail-closed (transport error/cap breach -> BundleError, caller
SKIP-defaults; partial walks marked incomplete, never clean).

NOT a verdict: returns cluster structure + shares. The filter decides
thresholds later with measured data (starting points 15% investigate /
25% strong come from the research brief, recorded here as config, not fate).

Known limitation (documented, not hidden): very-high-volume CEX hot
wallets can cluster organic buyers. Mitigation wired in: an ancestor must
fund >= MIN_SHARED (2) top holders AND within the time window to cluster;
deeper disambiguation (known-CEX list) is a later operator input.
"""
from __future__ import annotations

import logging
import time
import urllib.request
import json as _json

logger = logging.getLogger(__name__)

TIMEOUT_S = 15.0
MAX_TX_FETCHES = 60  # hard budget per clusters() call (free-tier discipline)


class BundleError(RuntimeError):
    """Typed failure: caller SKIP-defaults, never treats partial as clean."""


def _default_post(rpc_url: str, timeout_s: float = TIMEOUT_S):
    def post(payload: dict):
        body = _json.dumps(payload).encode()
        req = urllib.request.Request(
            rpc_url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                out = _json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            raise BundleError(f"transport: {type(e).__name__}: {e}") from e
        if not isinstance(out, dict) or "result" not in out:
            raise BundleError("transport: malformed response")
        return out["result"]
    return post


class BundleGraph:
    """One-shot funding walks (no lived state; results are snapshots)."""

    def __init__(self, rpc_url: str | None = None, post=None,
                 top_n: int = 15, max_sigs: int = 5,
                 window_s: int = 1800,
                 flag_pct: float = 15.0, strong_pct: float = 25.0):
        from .meme_feed import resolve_helius_rpc
        self.rpc_url = rpc_url or resolve_helius_rpc()
        self._post = post or _default_post(self.rpc_url)
        self.top_n = top_n
        self.max_sigs = max_sigs
        self.window_s = window_s
        self.flag_pct = flag_pct
        self.strong_pct = strong_pct
        self._fetches = 0

    def _call(self, method: str, params: list):
        self._fetches += 1
        if self._fetches > MAX_TX_FETCHES:
            raise BundleError(f"fetch budget exceeded ({MAX_TX_FETCHES})")
        try:
            return self._post({"jsonrpc": "2.0", "id": 1,
                               "method": method, "params": params})
        except BundleError:
            raise
        except Exception as e:
            raise BundleError(f"{method}: {type(e).__name__}: {e}") from e

    def top_holders(self, mint: str) -> tuple[list, int]:
        """(holders [{address, amount}], supply_raw). Raises BundleError."""
        res = self._call("getTokenLargestAccounts", [mint])
        accounts = (res or {}).get("value", []) if isinstance(res, dict) else []
        if not accounts:
            raise BundleError(f"no holder data for {mint}")
        holders = [{"address": a.get("address"),
                    "amount": int(a.get("amount", 0))}
                   for a in accounts[:self.top_n]
                   if isinstance(a, dict) and a.get("address")]
        if not holders:
            raise BundleError(f"no usable holders for {mint}")
        return holders, self._supply(mint)

    def _supply(self, mint: str) -> int:
        from .meme_feed import parse_mint_authorities
        res = self._call("getAccountInfo", [mint, {"encoding": "base64"}])
        if not res or not res.get("value"):
            raise BundleError(f"mint account missing: {mint}")
        data = res["value"].get("data", [])
        if not isinstance(data, list) or not data:
            raise BundleError("mint account has no data")
        return parse_mint_authorities(data[0])["supply_raw"]

    def funding_inflows(self, address: str) -> list:
        """Recent native-SOL transfers INTO address: [{source, slot, time}].
        Top-level + inner system-transfer instructions, jsonParsed."""
        res = self._call("getSignaturesForAddress",
                         [address, {"limit": self.max_sigs}])
        if not isinstance(res, list):
            raise BundleError("signatures response not a list")
        inflows = []
        for entry in res:
            if not isinstance(entry, dict) or entry.get("err"):
                continue
            sig = entry.get("signature")
            if not sig:
                continue
            tx = self._call("getTransaction",
                            [sig, {"encoding": "jsonParsed",
                                   "maxSupportedTransactionVersion": 0}])
            if not isinstance(tx, dict):
                continue
            t = (tx.get("blockTime") or 0)
            for ix in self._transfer_ixs(tx):
                if ix.get("destination") == address and ix.get("source"):
                    inflows.append({"source": ix["source"],
                                    "slot": tx.get("slot", 0), "time": t})
        return inflows

    @staticmethod
    def _transfer_ixs(tx: dict) -> list:
        """System-program transfer instructions, top-level + inner."""
        out = []

        def grab(ix):
            if not isinstance(ix, dict):
                return
            if ix.get("program") == "system" and ix.get("parsed", {}).get(
                    "type") == "transfer":
                info = ix["parsed"].get("info", {})
                if info.get("destination") and info.get("source"):
                    out.append({"destination": info["destination"],
                                "source": info["source"]})

        msg = tx.get("transaction", {}).get("message", {})
        for ix in msg.get("instructions", []) or []:
            grab(ix)
        meta = tx.get("meta", {}) or {}
        for inner in meta.get("innerInstructions", []) or []:
            for ix in (inner or {}).get("instructions", []) or []:
                grab(ix)
        return out

    def clusters(self, mint: str) -> dict:
        """Cluster top holders by shared direct funders (1 hop).
        Returns {clusters, max_cluster_share_pct, complete, ...}.
        Shares exclude nothing here (pool/curve exclusion is the filter's
        job with its venue knowledge); amounts are raw units."""
        holders, supply = self.top_holders(mint)
        if supply <= 0:
            raise BundleError("zero supply")
        amounts = {h["address"]: h["amount"] for h in holders}
        # Hop 1: funders per holder. A failed walk marks the result
        # incomplete (never clean): absence of evidence is not evidence
        # of independence, and a throttled run must not report "no bundles".
        funders: dict[str, list] = {}
        skipped: list[str] = []
        for h in holders:
            try:
                funders[h["address"]] = self.funding_inflows(h["address"])
            except BundleError as e:
                logger.warning(f"bundle hop1 skipped {h['address']}: {e}")
                funders[h["address"]] = []
                skipped.append(h["address"])
        # Invert: ancestor -> [(holder, time)].
        by_ancestor: dict[str, list] = {}
        for holder, flows in funders.items():
            for f in flows:
                by_ancestor.setdefault(f["source"], []).append(
                    (holder, f.get("time") or 0))
        clusters = []
        for ancestor, members in by_ancestor.items():
            if len({m for m, _ in members}) < 2:
                continue
            times = [t for _, t in members if t]
            if times and max(times) - min(times) > self.window_s:
                continue
            held = sum(amounts[m] for m, _ in members)
            clusters.append({
                "ancestor": ancestor,
                "members": sorted({m for m, _ in members}),
                "share_pct": held / supply * 100.0,
            })
        clusters.sort(key=lambda c: -c["share_pct"])
        top = clusters[0]["share_pct"] if clusters else 0.0
        return {
            "mint": mint,
            "holders_examined": len(holders),
            "supply_raw": supply,
            "clusters": clusters,
            "max_cluster_share_pct": top,
            "flag": top >= self.strong_pct,
            "investigate": top >= self.flag_pct,
            "complete": not skipped,
            "skipped_holders": sorted(skipped),
            "fetches": self._fetches,
        }
