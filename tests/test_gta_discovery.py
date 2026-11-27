from datetime import datetime, timezone

from src.gta_discovery import enrich_gta_mint, scan_round
from src.gta_event import GtaEventPolicy
from src.gta_external_safety import ExternalSafetySignal


def _policy():
    return GtaEventPolicy(
        enabled=True,
        start_utc=datetime(2026, 11, 18, tzinfo=timezone.utc),
        end_utc=datetime(2026, 11, 22, tzinfo=timezone.utc),
        max_total_usd=100,
        max_per_token_usd=10,
        max_open_positions=2,
    )


def test_scan_deduplicates_and_logs_fail_closed_enrichment():
    calls = []

    def enrich(mint):
        calls.append(mint)
        return {"symbol": "GTA6", "liquidity_usd": 1000}

    rows = scan_round(
        _policy(),
        [{"mint": "m1"}, {"mint": "m1"}, {"mint": "m2"}],
        enrich=enrich,
        now=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )
    assert calls == ["m1", "m2"]
    assert len(rows) == 2
    assert all(row["allowed"] is False for row in rows)
    assert all(row["reason"] == "safety enrichment incomplete" for row in rows)


def test_scan_records_keyword_mismatch_without_broadcast_path():
    rows = scan_round(
        _policy(),
        [{"mint": "m1", "name": "Unrelated", "symbol": "ABC"}],
        enrich=lambda _mint: {
            "liquidity_usd": 1000,
            "top10_hold_pct": 10,
            "holder_count": 1000,
            "mint_authority_disabled": True,
            "freeze_authority_disabled": True,
            "honeypot": False,
            "transfer_tax_pct": 0.0,
            "bundled_buy_pct": 10.0,
            "creator_past_rugs": 0,
            "creator_launch_count": 2,
            "lp_locked": True,
        },
        now=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )
    assert rows[0]["allowed"] is False
    assert rows[0]["reason"] == "theme mismatch"


def test_bundle_graph_only_populates_complete_bounded_sample(monkeypatch):
    class _Graph:
        def __init__(self):
            self.calls = []

        def clusters(self, mint):
            self.calls.append(mint)
            return {"complete": True, "max_cluster_share_pct": 12.5}

    graph = _Graph()
    monkeypatch.setattr("src.gta_discovery.enrich_mint", lambda _mint: {
        "liquidity_usd": 1000,
        "price_usd": 1,
        "symbol": "GTA6",
    })
    class _Snap:
        top10_hold_pct = 10.0
        holder_count = 200
        mint_authority_renounced = True
        freeze_authority_renounced = True
        creator_address = None

    class _Feed:
        def snapshot_for_mint(self, _mint, _extra):
            return _Snap()

    monkeypatch.setattr("src.meme_feed.HeliusFeed", _Feed)
    monkeypatch.setattr("src.meme_jupiter.token_info", lambda _mint: {})
    monkeypatch.setattr("src.meme_jupiter.round_trip_cost_pct", lambda *a, **k: None)
    # Exercise the adapter directly so the test never performs network I/O.
    row = enrich_gta_mint("m1", bundle_graph=graph)
    assert row["bundled_buy_pct"] == 12.5
    assert row["_enrichment_sources"]["bundle_graph"] == "ok"
    assert row["transfer_tax_evidence"]["status"] == "unknown"
    assert graph.calls == ["m1"]


class _MetadataSnap:
    top10_hold_pct = 10.0
    holder_count = 200
    mint_authority_renounced = True
    freeze_authority_renounced = True
    creator_address = None


class _MetadataFeed:
    """Snapshot that carries a Token-2022 TokenMetadata extension."""

    def snapshot_for_mint(self, _mint, _extra):
        snap = _MetadataSnap()
        snap.token2022_extensions = {
            "extension_types": ("TokenMetadata", "TransferFeeConfig"),
            "hard_risk_flags": ("transfer_hook",),
            "token_metadata": True,
            "transfer_tax_pct": 2.5,
        }
        return snap


class _PlainSnap:
    top10_hold_pct = 10.0
    holder_count = 200
    mint_authority_renounced = True
    freeze_authority_renounced = True
    creator_address = None


class _PlainFeed:
    """Snapshot with no token2022 extensions (classic SPL)."""

    def snapshot_for_mint(self, _mint, _extra):
        snap = _PlainSnap()
        snap.token2022_extensions = None
        return snap


def test_token_metadata_surfaces_on_candidate(monkeypatch):
    monkeypatch.setattr("src.gta_discovery.enrich_mint", lambda _mint: {
        "liquidity_usd": 1000,
        "price_usd": 1,
        "symbol": "GTA6",
    })
    monkeypatch.setattr("src.meme_feed.HeliusFeed", _MetadataFeed)
    monkeypatch.setattr("src.meme_jupiter.token_info", lambda _mint: {})
    monkeypatch.setattr("src.meme_jupiter.round_trip_cost_pct", lambda *a, **k: None)
    row = enrich_gta_mint("m1")
    assert row["token2022_token_metadata"] is True
    assert row["token2022_extension_types"] == ("TokenMetadata", "TransferFeeConfig")
    assert "transfer_hook" in row["token2022_hard_risk_flags"]
    # Metadata is recorded but never enters hard_risk_flags.
    assert "token_metadata" not in row["token2022_hard_risk_flags"]


def test_no_metadata_means_false_on_candidate(monkeypatch):
    monkeypatch.setattr("src.gta_discovery.enrich_mint", lambda _mint: {
        "liquidity_usd": 1000,
        "price_usd": 1,
        "symbol": "GTA6",
    })
    monkeypatch.setattr("src.meme_feed.HeliusFeed", _PlainFeed)
    monkeypatch.setattr("src.meme_jupiter.token_info", lambda _mint: {})
    monkeypatch.setattr("src.meme_jupiter.round_trip_cost_pct", lambda *a, **k: None)
    row = enrich_gta_mint("m1")
    assert row["token2022_token_metadata"] is False
    assert row["token2022_extension_types"] == ()


def test_external_safety_evidence_is_wired_without_promoting_gate(monkeypatch):
    monkeypatch.setattr("src.gta_discovery.enrich_mint", lambda _mint: {
        "liquidity_usd": 1000, "price_usd": 1, "symbol": "GTA6",
    })
    monkeypatch.setattr("src.meme_feed.HeliusFeed", _PlainFeed)
    monkeypatch.setattr("src.meme_jupiter.token_info", lambda _mint: {})
    monkeypatch.setattr("src.meme_jupiter.round_trip_cost_pct", lambda *a, **k: None)
    row = enrich_gta_mint("m1", external_signals=(
        ExternalSafetySignal("goplus", "ok", False, 0.0, observed_at=10),
        ExternalSafetySignal("rugcheck", "unsafe", True, 20.0, observed_at=11),
    ))
    assert row["external_safety_evidence"]["status"] == "unknown"
    assert row["external_safety_evidence"]["reason"] == "provider conflict"
    assert row["transfer_tax_evidence"]["status"] == "unknown"
