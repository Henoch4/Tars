"""Memecoin snapshot feed (Book 2, paper v1) — Helius RPC -> TokenSnapshot.

Discovery policy (deliberate): NO sniper discovery here. The operator
supplies candidate mints (watchlist from their own research); this adapter
fetches what the chain proves about each one — mint authorities, supply,
holder concentration — and maps it onto the scream filter's snapshot.
Unobservable fields stay None so the filter SKIP-defaults them. Automated
new-pool discovery (webhooks/polling) is a later slice, not this one:
the edge is pickiness, not speed.

Auth: HELIUS_RPC_URL (full URL, key embedded) preferred, else HELIUS_API_KEY
(assembled into the mainnet endpoint). Missing both raises — a feed with
no key must never silently degrade to public RPC (different rate limits,
different trust). Keys never logged.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import struct
import urllib.error
import urllib.request

from .meme_satellite import TokenSnapshot

logger = logging.getLogger(__name__)

HELIUS_MAINNET = "https://mainnet.helius-rpc.com/?api-key={key}"
TIMEOUT_S = 20


class FeedError(RuntimeError):
    """Typed feed failure: callers treat it as 'no snapshots this round'
    (zero candidates), never as a pass."""


def resolve_helius_rpc(explicit: str | None = None) -> str:
    """RPC URL for Helius calls. Explicit arg wins (tests), then
    HELIUS_RPC_URL, then HELIUS_API_KEY assembled into mainnet."""
    if explicit and explicit.strip():
        return explicit.strip()
    url = (os.getenv("HELIUS_RPC_URL") or "").strip()
    if url:
        return url
    key = (os.getenv("HELIUS_API_KEY") or "").strip()
    if key:
        return HELIUS_MAINNET.format(key=key)
    raise FeedError(
        "no Helius credentials: set HELIUS_RPC_URL or HELIUS_API_KEY "
        "(repo-root .env local, /etc/tars/agent.env on the VPS)"
    )


def parse_mint_authorities(data_b64: str) -> dict:
    """Parse SPL Token Mint layout (82 bytes): COption mint authority,
    supply u64, decimals, initialized flag, COption freeze authority.
    Returns renounced flags + supply + decimals. Raises FeedError on
    short/malformed data (never guess from a truncated buffer)."""
    try:
        raw = base64.b64decode(data_b64)
    except Exception as e:
        raise FeedError(f"mint data not base64-decodable: {e}") from e
    if len(raw) < 82:
        raise FeedError(f"mint account too short ({len(raw)} bytes, need 82)")
    mint_disc = struct.unpack_from("<I", raw, 0)[0]
    supply = struct.unpack_from("<Q", raw, 36)[0]
    decimals = raw[44]
    freeze_disc = struct.unpack_from("<I", raw, 46)[0]
    return {
        "mint_authority_renounced": mint_disc == 0,
        "freeze_authority_renounced": freeze_disc == 0,
        "supply_raw": supply,
        "decimals": decimals,
    }


class HeliusFeed:
    """Thin JSON-RPC client mapping chain state onto TokenSnapshots."""

    def __init__(self, rpc_url: str | None = None, timeout_s: float = TIMEOUT_S,
                 post=None):
        self.rpc_url = resolve_helius_rpc(rpc_url)
        self.timeout_s = timeout_s
        self._post = post or self._http_post

    def _http_post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.rpc_url, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                out = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise FeedError(
                f"helius rpc transport failed: HTTP {e.code}") from e
        except Exception as e:
            raise FeedError(f"helius rpc transport failed: {type(e).__name__}") from e
        if isinstance(out, dict) and out.get("error"):
            raise FeedError(f"helius rpc error: {out['error']}")
        return out

    def _call(self, method: str, params: list) -> object:
        out = self._post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(out, dict) or "result" not in out:
            raise FeedError(f"helius {method}: malformed response")
        return out["result"]

    def mint_info(self, mint: str) -> dict:
        """Authorities + supply + decimals for a token mint."""
        res = self._call("getAccountInfo", [mint, {"encoding": "base64"}])
        resd = res if isinstance(res, dict) else {}
        if not resd or not resd.get("value"):
            raise FeedError(f"mint account missing: {mint}")
        data = resd["value"].get("data", [])
        if not isinstance(data, list) or len(data) < 1:
            raise FeedError(f"mint account has no data: {mint}")
        return parse_mint_authorities(data[0])

    def top_holder_share_pct(self, mint: str, supply_raw: int,
                              decimals: int, top_n: int = 10,
                              exclude: set | None = None) -> tuple[float, int]:
        """(top-N share %, VISIBLE holder accounts) from getTokenLargestAccounts.
        Amounts are raw units; supply likewise — decimals cancel out. The
        count is len(accounts) of the FULL page (not the top-N slice): below
        the page size it proves few holders exist; a full page means that
        many or more. `exclude` removes program-custody vaults (curve/pool
        accounts) from the share math — they are not organic holders — but
        the visible count is unchanged (conservative floor semantics)."""
        res = self._call("getTokenLargestAccounts", [mint])
        accounts = (res or {}).get("value", []) if isinstance(res, dict) else []
        if not accounts:
            raise FeedError(f"no holder data for {mint}")
        if supply_raw <= 0:
            raise FeedError(f"zero supply for {mint}")
        ranked = sorted(accounts,
                        key=lambda a: int(a.get("amount", 0)), reverse=True)
        if exclude:
            ranked = [a for a in ranked
                      if a.get("address") not in exclude]
        ranked = ranked[:top_n]
        held = sum(int(a.get("amount", 0)) for a in ranked)
        return held / supply_raw * 100.0, len(accounts)

    def pool_vault_accounts(self, mint: str, pool: str) -> set:
        """Token accounts OWNED by the pool/curve state address — the
        vaults holding program custody of this mint. Direct address
        matching never fires (live probe 2026-10-17, 5/5 rejected mints:
        pair-in-list=False) because getTokenLargestAccounts returns
        TOKEN-ACCOUNT pubkeys while DexScreener's pairAddress is the pool
        STATE account; one ownership lookup bridges the two. Fail-soft:
        any failure returns an empty set and the caller falls back to the
        direct {pool} match — concentration then stays inflated, i.e.
        rejects MORE, never less (house fail-closed direction)."""
        try:
            res = self._call("getTokenAccountsByOwner",
                             [pool, {"mint": mint},
                              {"encoding": "base64"}])
        except FeedError:
            return set()
        resd = res if isinstance(res, dict) else {}
        out = set()
        for v in (resd.get("value", []) or []):
            if isinstance(v, dict):
                pk = v.get("pubkey")
                if isinstance(pk, str) and pk:
                    out.add(pk)
        return out

    def owner_token_balance_raw(self, mint: str, owner: str) -> int:
        """Sum of `owner`'s token-account balances for `mint`, in raw units.
        Sums across multiple accounts per (owner, mint) and covers both the
        classic SPL Token and Token-2022 programs (the amount is a u64 at
        offset 64 in both token-account layouts). Returns 0 when the owner
        holds none — a real, actionable value. Raises FeedError on transport
        failure so the caller leaves the field UNOBSERVED rather than
        recording a false 0.0 (house fail-closed direction)."""
        res = self._call("getTokenAccountsByOwner",
                         [owner, {"mint": mint}, {"encoding": "base64"}])
        if res is not None and not isinstance(res, dict):
            raise FeedError("getTokenAccountsByOwner: unexpected response shape")
        total = 0
        for v in ((res or {}).get("value", []) or []):
            if not isinstance(v, dict):
                continue
            data = (v.get("account") or {}).get("data")
            b64 = data[0] if isinstance(data, list) and data else None
            if not isinstance(b64, str):
                continue
            try:
                raw = base64.b64decode(b64)
            except Exception:
                continue
            if len(raw) >= 72:
                total += struct.unpack_from("<Q", raw, 64)[0]
        return total

    def snapshot_for_mint(self, mint: str, extra: dict | None = None) -> TokenSnapshot:
        """Build the scream filter's snapshot. Chain-provable fields come
        from RPC; everything else (age, liquidity, taxes, creator history,
        graduation) arrives via `extra`. Creator share is computed ON-CHAIN
        (deployer balance / supply) when not supplied — the free,
        deterministic replacement for the dead RugCheck supplier."""
        if not isinstance(mint, str) or not mint.strip():
            raise FeedError(f"invalid mint {mint!r}")
        extra = dict(extra or {})
        info = self.mint_info(mint)
        pool = extra.get("pool_address")
        # Exclude program custody from the concentration math: the pool
        # STATE address itself (never matches — kept as fail-soft fallback)
        # UNION the token accounts the pool actually owns (the real vault,
        # 82-96% of supply on pumpswap graduates).
        exclude = None
        if pool:
            exclude = {pool} | self.pool_vault_accounts(mint, pool)
        top10, holders = self.top_holder_share_pct(
            mint, info["supply_raw"], info["decimals"], exclude=exclude)
        # Creator share: prefer a supplied value (RugCheck, when alive);
        # else compute on-chain = the deployer's CURRENT balance / supply.
        # A transport failure leaves it None (SKIP-default), never a
        # guessed 0.0.
        creator_address = extra.get("creator_address")
        creator_hold_pct = extra.get("creator_hold_pct")
        if (creator_hold_pct is None and isinstance(creator_address, str)
                and creator_address and info["supply_raw"] > 0):
            try:
                held_raw = self.owner_token_balance_raw(mint, creator_address)
                creator_hold_pct = held_raw / info["supply_raw"] * 100.0
            except FeedError:
                creator_hold_pct = None
        snap = TokenSnapshot(
            mint=mint,
            symbol=extra.get("symbol"),
            top10_hold_pct=top10,
            holder_count=holders,
            creator_address=creator_address,
            rugcheck_rugged=extra.get("rugcheck_rugged"),
            rugcheck_score=extra.get("rugcheck_score"),
            mcap_usd=extra.get("mcap_usd"),
            block0_buyers=extra.get("block0_buyers"),
            dev_sold_minute1=extra.get("dev_sold_minute1"),
            creator_hold_pct=creator_hold_pct,
            lp_locked=extra.get("lp_locked"),
            mint_authority_renounced=info["mint_authority_renounced"],
            freeze_authority_renounced=info["freeze_authority_renounced"],
            age_s=extra.get("age_s"),
            liquidity_usd=extra.get("liquidity_usd"),
            buy_tax_pct=extra.get("buy_tax_pct"),
            sell_tax_pct=extra.get("sell_tax_pct"),
            round_trip_cost_pct=extra.get("round_trip_cost_pct"),
            organic_score=extra.get("organic_score"),
            creator_past_rugs=extra.get("creator_past_rugs"),
            creator_launches=extra.get("creator_launches"),
            graduated=bool(extra.get("graduated", False)),
        )
        return snap

    def snapshot_many(self, mints: list, extras: dict | None = None) -> list:
        """Snapshot a watchlist; per-mint FeedError is recorded as a
        SKIP-shaped snapshot note upstream — one bad mint never aborts
        the round (batch guard, same doctrine as the trading cycle)."""
        extras = extras or {}
        out: list[tuple[str, TokenSnapshot | str]] = []
        for mint in mints:
            try:
                out.append(("ok", self.snapshot_for_mint(
                    mint, extras.get(mint))))
            except FeedError as e:
                logger.warning(f"snapshot skipped {mint}: {e}")
                out.append(("skipped", str(e)))
        return out
