"""Read-only GTA candidate discovery and policy evaluation.

This is intentionally a data-plane component: it discovers and enriches
Solana candidates, then records the policy verdict. It never loads a key,
quotes a swap, or calls a broadcast endpoint.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Any

from .gta_event import GtaEventPolicy, GtaDecision, evaluate_candidate
from .gta_enrichment import validate_safety_payload, combine_transfer_tax_evidence
from .meme_discovery import DiscoveryError, discover_candidates, enrich_mint


def enrich_gta_mint(mint: str, *, bundle_graph=None, external_signals=()) -> dict[str, Any]:
    """Compose the best available read-only safety enrichment for GTA.

    DexScreener remains the market-data source. Helius is then used for
    chain-truth authorities and holder concentration, which the previous GTA
    loop never requested. Optional Jupiter/RugCheck/history sources add
    creator and sell-route evidence when available. Every source is recorded
    explicitly; missing data stays missing and therefore fails closed.
    """
    candidate: dict[str, Any] = {"mint": mint}
    sources: dict[str, str] = {}
    errors: dict[str, str] = {}
    jupiter_tax_pct = None
    round_trip = None

    try:
        candidate.update(enrich_mint(mint))
        sources["dexscreener"] = "ok"
    except Exception as exc:
        errors["dexscreener"] = f"{type(exc).__name__}:{str(exc)[:160]}"

    try:
        from .meme_feed import HeliusFeed

        snap = HeliusFeed().snapshot_for_mint(mint, candidate)
        candidate.update({
            "top10_hold_pct": snap.top10_hold_pct,
            "holder_count": snap.holder_count,
            "mint_authority_disabled": snap.mint_authority_renounced,
            "freeze_authority_disabled": snap.freeze_authority_renounced,
            "creator_address": snap.creator_address,
        })
        token2022 = snap.token2022_extensions or {}
        if token2022:
            if (candidate.get("transfer_tax_pct") is None
                    and token2022.get("transfer_tax_pct") is not None):
                candidate["transfer_tax_pct"] = token2022["transfer_tax_pct"]
            candidate["token2022_extension_types"] = token2022.get("extension_types", ())
            candidate["token2022_hard_risk_flags"] = token2022.get("hard_risk_flags", ())
            # TokenMetadata is recorded in the audit trail but is irrelevant to
            # the fee/hook gate: it never enters hard_risk_flags and never
            # blocks a candidate. Malformed metadata already fails closed in
            # the parser; its mere presence is only audited here.
            candidate["token2022_token_metadata"] = bool(token2022.get("token_metadata", False))
            sources["token2022_extensions"] = "ok"
        else:
            sources["token2022_extensions"] = "classic_spl"
            candidate["token2022_extension_types"] = ()
            candidate["token2022_hard_risk_flags"] = ()
            candidate["token2022_token_metadata"] = False
        sources["helius"] = "ok"
    except Exception as exc:
        errors["helius"] = f"{type(exc).__name__}:{str(exc)[:160]}"

    # Jupiter's token audit is a second authority source. Use it only for
    # fields it explicitly reports; it must not overwrite a chain-truth value
    # already supplied by Helius.
    try:
        from .meme_jupiter import token_info

        profile = token_info(mint)
        if profile:
            for field in (
                "mint_authority_disabled", "freeze_authority_disabled",
                "honeypot", "transfer_tax_pct",
            ):
                if candidate.get(field) is None and profile.get(field) is not None:
                    candidate[field] = profile[field]
            jupiter_tax_pct = profile.get("transfer_tax_pct")
            if candidate.get("holder_count") is None:
                candidate["holder_count"] = profile.get("holder_count")
            if candidate.get("top10_hold_pct") is None:
                candidate["top10_hold_pct"] = profile.get("top_holders_pct")
            if profile.get("dev") and not candidate.get("creator_address"):
                candidate["creator_address"] = profile["dev"]
            if profile.get("dev_mints") is not None:
                candidate["creator_launch_count"] = profile["dev_mints"]
            sources["jupiter_audit"] = "ok"
        else:
            sources["jupiter_audit"] = "unavailable"
    except Exception as exc:
        errors["jupiter_audit"] = f"{type(exc).__name__}:{str(exc)[:160]}"

    # LP safety is only queried for graduated pools, where a market record
    # exists to inspect. The adapter accepts explicit lock/burn coverage from
    # RugCheck; it never derives safety from liquidity or venue alone.
    try:
        from .meme_rugcheck import rugcheck_mint

        lp = rugcheck_mint(mint)
        for field in ("lp_locked", "lp_locked_pct", "lp_burned_pct", "lp_provider"):
            if lp.get(field) is not None:
                candidate[field] = lp[field]
        sources["lp_safety"] = (
            "ok" if candidate.get("lp_locked") is not None else "unavailable")
    except Exception as exc:
        errors["lp_safety"] = f"{type(exc).__name__}:{str(exc)[:160]}"

    # Jupiter's round-trip quote is evidence about route sellability, not a
    # transfer-tax measurement. We retain it as a metric and only clear the
    # honeypot field when both legs are measurable; tax/bundle/LP fields remain
    # unobserved unless a dedicated source proves them.
    try:
        from .meme_jupiter import round_trip_cost_pct

        round_trip = round_trip_cost_pct(
            mint, price_usd=candidate.get("price_usd"))
        candidate["round_trip_cost_pct"] = round_trip
        if round_trip is not None:
            candidate["honeypot"] = False
        sources["jupiter_round_trip"] = "ok" if round_trip is not None else "unavailable"
    except Exception as exc:
        errors["jupiter_round_trip"] = type(exc).__name__

    # Creator history is local, auditable state. It is only populated when a
    # creator address is known; no creator is treated as clean by default.
    try:
        creator = candidate.get("creator_address")
        if isinstance(creator, str) and creator:
            from .meme_history import creator_launch_count, load_history, rugs_for_creator

            history = load_history("data/meme_satellite/deployer_history.json")
            candidate["creator_past_rugs"] = rugs_for_creator(history, creator)
            candidate["creator_launch_count"] = creator_launch_count(
                history, creator, mint)
            sources["creator_history"] = "ok"
        else:
            sources["creator_history"] = "unavailable"
    except Exception as exc:
        errors["creator_history"] = type(exc).__name__

    # Optional bounded funding graph. Partial walks remain unknown and fail
    # closed; a clean result is recorded as a concentration proxy, not proof
    # of common ownership.
    if bundle_graph is not None:
        try:
            bundle = bundle_graph.clusters(mint)
            if bundle.get("complete") is True:
                candidate["bundled_buy_pct"] = bundle.get("max_cluster_share_pct")
                sources["bundle_graph"] = "ok"
            else:
                sources["bundle_graph"] = "incomplete"
                errors["bundle_graph"] = "incomplete_holder_walk"
        except Exception as exc:
            errors["bundle_graph"] = f"{type(exc).__name__}:{str(exc)[:160]}"
    else:
        sources["bundle_graph"] = "not_sampled"

    tax_evidence = combine_transfer_tax_evidence(
        token2022=candidate.get("token2022_extensions"),
        jupiter_tax_pct=jupiter_tax_pct,
        simulation_tax_pct=round_trip,
    )
    candidate["transfer_tax_evidence"] = tax_evidence.as_dict()
    if external_signals:
        from .gta_external_safety import combine_external_signals
        external = combine_external_signals(*tuple(external_signals))
        candidate["external_safety_evidence"] = external.as_dict()
        sources["external_safety"] = external.status
    else:
        candidate["external_safety_evidence"] = {
            "status": "unknown", "reason": "not_sampled", "sources": (),
            "observed_at": time.time(),
        }
        sources["external_safety"] = "not_sampled"

    candidate["_enrichment_sources"] = sources
    candidate["_enrichment_errors"] = errors
    return candidate


def scan_round(
    policy: GtaEventPolicy,
    candidates: list[dict[str, Any]],
    *,
    enrich: Callable[[str], dict[str, Any]] = enrich_gta_mint,
    now=None,
    spent_usd: float = 0.0,
    open_positions: int = 0,
    bundle_budget: int = 0,
) -> list[dict[str, Any]]:
    """Enrich and evaluate candidates, returning auditable observations."""
    observations: list[dict[str, Any]] = []
    seen: set[str] = set()
    bundle_graph = None
    if enrich is enrich_gta_mint and bundle_budget > 0:
        try:
            from .meme_bundle import BundleGraph
            bundle_graph = BundleGraph(top_n=10, max_sigs=2)
        except Exception:
            bundle_budget = 0
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        mint = raw.get("mint")
        if not isinstance(mint, str) or not mint.strip() or mint in seen:
            continue
        seen.add(mint)
        record: dict[str, Any] = {"mint": mint, "ts": time.time()}
        try:
            if enrich is enrich_gta_mint:
                enriched = enrich(
                    mint,
                    bundle_graph=(bundle_graph
                                  if len(observations) < bundle_budget else None),
                )
            else:
                enriched = enrich(mint)
            if not isinstance(enriched, dict):
                raise ValueError("enrichment returned non-object")
            candidate = {**raw, **enriched, "mint": mint}
            record.update({k: candidate.get(k) for k in (
                "name", "symbol", "liquidity_usd", "volume_24h_usd",
                "age_s", "graduated", "price_usd", "venue", "mcap_usd",
            ) if k in candidate})
            record["enrichment_sources"] = candidate.get("_enrichment_sources", {})
            record["enrichment_errors"] = candidate.get("_enrichment_errors", {})
            record["transfer_tax_evidence"] = candidate.get("transfer_tax_evidence", {
                "status": "unknown", "tax_pct": None,
                "sources": (), "reason": "evidence not recorded",
            })
            completeness = validate_safety_payload(candidate)
            if not completeness.complete:
                missing = list(completeness.missing) + list(completeness.invalid)
                record["enrichment_missing"] = missing
                decision = GtaDecision(
                    False,
                    "safety enrichment incomplete",
                    tuple(f"{field} unavailable or invalid" for field in missing),
                )
            else:
                decision = evaluate_candidate(
                    policy, candidate, spent_usd=spent_usd,
                    open_positions=open_positions, now=now,
                )
        except Exception as exc:  # data-plane failure is an auditable skip
            decision = GtaDecision(False, "enrichment failure", (type(exc).__name__,))
        record.update({
            "allowed": decision.allowed,
            "reason": decision.reason,
            "reasons": list(decision.reasons),
        })
        observations.append(record)
    return observations


def discover_and_scan(
    policy: GtaEventPolicy,
    *,
    limit: int = 25,
    discover: Callable[[int], list[dict[str, Any]]] = discover_candidates,
    enrich: Callable[[str], dict[str, Any]] = enrich_gta_mint,
    now=None,
    bundle_budget: int = 0,
) -> list[dict[str, Any]]:
    """Run one read-only discovery round; feed failures produce no guesses."""
    candidates = discover(limit)
    return scan_round(
        policy, candidates, enrich=enrich, now=now,
        bundle_budget=bundle_budget,
    )


def append_observations(path: str | Path, observations: list[dict[str, Any]]) -> None:
    """Append JSONL observations without mutating positions or wallet state."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for observation in observations:
            handle.write(json.dumps(observation, sort_keys=True, default=str) + "\n")
