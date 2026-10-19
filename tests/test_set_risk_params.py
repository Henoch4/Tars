"""Port tests: set_risk_params plan/preview logic (no network, no keys)."""
import pytest

from scripts.set_risk_params import (
    build_plan,
    check_tightening,
    format_plan,
    parse_args,
    parse_asset_cap,
)


def test_parse_asset_cap_ok():
    assert parse_asset_cap("BTC-USDT-SWAP:2000") == ("BTC-USDT-SWAP", 2000.0)


@pytest.mark.parametrize("bad", [
    "BTC-USDT-SWAP",  # no amount
    "BTC:100:extra",  # two colons
    ":2000",  # empty asset
    "BTC:abc",  # stringified amount
    "BTC:0",  # zero disables nothing
    "BTC:-50",
    "BTC:True",
    "",
    None,
])
def test_parse_asset_cap_rejects(bad):
    with pytest.raises(ValueError):
        parse_asset_cap(bad)


def test_build_plan_flags_beat_env(monkeypatch):
    monkeypatch.setenv("MAX_POSITION_USD", "9999")
    args = parse_args(["--max-position-usd", "1000"])
    plan = build_plan(args)
    assert plan["max_position_usd"] == 1000.0


def test_build_plan_env_fallback_and_defaults(monkeypatch):
    for k in ("MAX_POSITION_USD", "MAX_DAILY_LOSS_USD", "MAX_LEVERAGE",
              "MIN_CONFIDENCE_BPS", "XLAYER_RPC_URL", "AUDIT_CONTRACT_ADDRESS"):
        monkeypatch.delenv(k, raising=False)
    plan = build_plan(parse_args([]))
    assert plan["max_position_usd"] == 5000.0
    assert plan["max_leverage_bps"] == 50000  # 5.0x, the historic bug class
    assert plan["chain_id"] == 1952  # X Layer, not tars-base's Sepolia default


def test_build_plan_collects_asset_caps():
    plan = build_plan(parse_args(["--asset-cap", "BTC-USDT-SWAP:2000",
                                  "--asset-cap", "SOL-USDT-SWAP:500"]))
    assert plan["asset_caps"] == [("BTC-USDT-SWAP", 2000.0),
                                  ("SOL-USDT-SWAP", 500.0)]


def test_check_tightening_warns_on_loosen():
    current = (5000_00000000, 500_00000000, 50000, 7000)
    assert check_tightening(current, {"max_position_usd": 6000.0,
                                      "max_leverage_bps": 50000}) != []
    assert check_tightening(current, {"max_position_usd": 4000.0,
                                      "max_leverage_bps": 50000}) == []
    assert check_tightening((0, 0, 0, 0), {"max_position_usd": 6000.0,
                                           "max_leverage_bps": 50000}) == []


def test_format_plan_names_everything():
    plan = build_plan(parse_args(["--asset-cap", "BTC-USDT-SWAP:2000"]))
    text = format_plan(plan)
    assert "BTC-USDT-SWAP" in text and "1952" in text


def test_scripts_importable_as_package(monkeypatch):
    """scripts/ has no __init__; the test imports it by path like CI does."""
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); "
         "import set_risk_params as s; "
         "print(s.build_plan(s.parse_args(['--dry-run']))['dry_run'])"],
        capture_output=True, text=True, cwd=".",
    )
    assert r.returncode == 0, r.stderr
    assert "True" in r.stdout
