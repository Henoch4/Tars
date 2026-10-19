"""5.2 verification: discrepancy detected and reported.

Pure-function tests on src.reconciliation.reconcile (no network, no keys):
vault totalAssets (6dp minimal units) vs OKX USDT balance. The endpoint
wiring (GET /api/v1/vault/reconciliation, POST /api/v1/vault/attest) is
covered by construction — these tests pin the economics they serve.
"""
from src.reconciliation import reconcile


def _vault(total_minimal, **kw):
    state = {"deployed": True, "total_assets": total_minimal,
             "pending_reserved": 0, "last_attestation": total_minimal,
             "attest_timelock": 3600}
    state.update(kw)
    return state


def _okx(usdt_eq, total_eq=None):
    return {"usdt_eq": float(usdt_eq),
            "total_eq": float(total_eq if total_eq is not None else usdt_eq),
            "details": []}


def test_discrepancy_detected_and_reported():
    r = reconcile(_vault(10_000_000_000), _okx(9500.0))
    assert r.vault_total_assets == 10_000_000_000
    assert r.okx_balance_usdt == 9500.0
    assert r.discrepancy_usdt == 500.0
    assert abs(r.discrepancy_pct - (500.0 / 9500.0 * 100)) < 1e-9
    assert r.read_error is None


def test_suggested_attestation_tracks_okx_balance():
    r = reconcile(_vault(10_000_000_000), _okx(9500.0))
    assert r.suggested_attestation == 9_500_000_000


def test_okx_down_surfaced_not_silent():
    r = reconcile(_vault(10_000_000_000), {})
    assert r.okx_available is False
    assert r.suggested_attestation is None
    assert r.discrepancy_usdt is None
    assert r.read_error is not None and "OKX" in r.read_error


def test_vault_read_error_keeps_deployed_true():
    r = reconcile({"deployed": True, "error": "rpc timeout"}, _okx(100.0))
    assert r.vault_deployed is True
    assert r.read_error is not None and "vault" in r.read_error


def test_undeployed_vault_reports_cleanly():
    r = reconcile({"deployed": False}, {})
    assert r.vault_deployed is False
    assert r.suggested_attestation is None
    assert r.discrepancy_usdt is None


def test_zero_okx_balance_no_pct_crash():
    r = reconcile(_vault(1_000_000_000), _okx(0.0))
    assert r.discrepancy_usdt == 1000.0
    assert r.discrepancy_pct is None
    assert r.suggested_attestation == 0
