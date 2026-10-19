"""Set (or correct) the on-chain risk parameters for this agent.

Ported from tars-base/scripts/set_risk_params.py (the runbook references
this script, but Tarstrade never had it): the one canonical way to write
risk params + per-asset caps to TradeAuditTrail.sol. Retargeted from
Base Sepolia to X Layer (chain 1952), extended with setAssetCap support
(the 2.9 per-asset breaker has no operator path without this), and given
a --dry-run preview so the mutation-gate preview step exists for chain
writes too.

Tightening ratchet (unchanged): once set for an address, position size
and leverage can only decrease — loosening needs a fresh agent address.

Usage:
    export AGENT_WALLET_PRIVATE_KEY=0x...   # fresh, rotated key, never committed
    export XLAYER_RPC_URL=https://testnet-rpc.xlayer.tech
    export AUDIT_CONTRACT_ADDRESS=0x...
    python scripts/set_risk_params.py --dry-run
    python scripts/set_risk_params.py [--asset-cap BTC-USDT-SWAP:2000 ...]

Env overrides (when flags are absent): MAX_POSITION_USD, MAX_DAILY_LOSS_USD,
MAX_LEVERAGE (float multiplier, 5.0 = 5x), MIN_CONFIDENCE_BPS.
"""
import argparse
import os
import sys

sys.path.insert(0, ".")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Write risk params + asset caps on-chain.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without touching the chain")
    ap.add_argument("--max-position-usd", type=float, default=None)
    ap.add_argument("--max-daily-loss-usd", type=float, default=None)
    ap.add_argument("--max-leverage", type=float, default=None,
                    help="float multiplier, e.g. 5.0 = 5x")
    ap.add_argument("--min-confidence-bps", type=int, default=None)
    ap.add_argument("--asset-cap", action="append", default=[],
                    help="ASSET:USD, repeatable (e.g. BTC-USDT-SWAP:2000)")
    ap.add_argument("--rpc-url", default=None)
    ap.add_argument("--contract-address", default=None)
    ap.add_argument("--chain-id", type=int, default=None)
    return ap.parse_args(argv)


def _env_float(name, default):
    raw = os.environ.get(name, "")
    if raw is None or str(raw).strip() == "":
        return default
    return float(raw)


def _env_int(name, default):
    raw = os.environ.get(name, "")
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def parse_asset_cap(spec: str) -> tuple:
    """ASSET:USD -> (asset, usd). Strict: no bare numbers, no negatives."""
    if not isinstance(spec, str) or spec.count(":") != 1:
        raise ValueError(f"asset cap must look like ASSET:USD, got {spec!r}")
    asset, _, amount = spec.partition(":")
    asset, amount = asset.strip(), amount.strip()
    if not asset:
        raise ValueError(f"asset cap needs a non-empty asset, got {spec!r}")
    try:
        usd = float(amount)
    except (TypeError, ValueError):
        raise ValueError(f"asset cap amount must be numeric, got {spec!r}") from None
    if not usd > 0:
        raise ValueError(f"asset cap must be positive (0 disables nothing; "
                         f"caps only tighten), got {spec!r}")
    return asset, usd


def build_plan(args) -> dict:
    """Pure plan from flags (else env, else current behavior). No I/O."""
    return {
        "max_position_usd": (args.max_position_usd
                             if args.max_position_usd is not None
                             else _env_float("MAX_POSITION_USD", 5000.0)),
        "max_daily_loss_usd": (args.max_daily_loss_usd
                               if args.max_daily_loss_usd is not None
                               else _env_float("MAX_DAILY_LOSS_USD", 500.0)),
        "max_leverage_bps": int((args.max_leverage
                                 if args.max_leverage is not None
                                 else _env_float("MAX_LEVERAGE", 5.0)) * 10000),
        "min_confidence_bps": (args.min_confidence_bps
                               if args.min_confidence_bps is not None
                               else _env_int("MIN_CONFIDENCE_BPS", 7000)),
        "asset_caps": [parse_asset_cap(s) for s in (args.asset_cap or [])],
        "rpc_url": (args.rpc_url or os.environ.get("XLAYER_RPC_URL", "").strip()),
        "contract_address": (args.contract_address
                             or os.environ.get("AUDIT_CONTRACT_ADDRESS", "").strip()),
        "chain_id": (args.chain_id if args.chain_id is not None
                     else int(os.environ.get("XLAYER_CHAIN_ID", "1952"))),
        "dry_run": bool(args.dry_run),
    }


def check_tightening(current: tuple, plan: dict) -> list:
    """Compare live on-chain params (position, loss, lev_bps, conf) against
    the plan. Returns warning strings; empty = no ratchet risk. Pure."""
    warnings = []
    if current[0] > 0:
        # agentRiskParams order: position, loss, leverage_bps, confidence.
        if plan["max_position_usd"] * 1e8 > current[0]:
            warnings.append(
                "max_position_usd exceeds the set value — the contract only "
                "tightens ('pos too high' revert likely; new agent address "
                "needed to loosen)")
        if plan["max_leverage_bps"] > current[2]:
            warnings.append(
                "max_leverage_bps exceeds the set value ('lev too high' "
                "revert likely)")
    return warnings


def format_plan(plan: dict) -> str:
    lines = [
        "planned on-chain write:",
        f"  max_position_usd   = ${plan['max_position_usd']:,.2f}",
        f"  max_daily_loss_usd = ${plan['max_daily_loss_usd']:,.2f}",
        f"  max_leverage_bps   = {plan['max_leverage_bps']} "
        f"({plan['max_leverage_bps'] / 10000:.2f}x)",
        f"  min_confidence_bps = {plan['min_confidence_bps']}",
    ]
    for asset, usd in plan["asset_caps"]:
        lines.append(f"  asset cap {asset} = ${usd:,.2f}")
    lines.append(f"  chain_id = {plan['chain_id']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = parse_args(argv)
    plan = build_plan(args)
    print(format_plan(plan))
    if plan["dry_run"]:
        print("\ndry-run: no chain writes made.")
        return 0

    private_key = os.environ.get("AGENT_WALLET_PRIVATE_KEY", "").strip()
    if not private_key or not plan["rpc_url"] or not plan["contract_address"]:
        print("Missing AGENT_WALLET_PRIVATE_KEY / rpc url / contract address "
              "(flags or env). Refusing to guess — aborting.",
              file=sys.stderr)
        return 2

    from src.audit_logger import OnchainLogger  # noqa: E402
    logger = OnchainLogger(
        rpc_url=plan["rpc_url"],
        contract_address=plan["contract_address"],
        private_key=private_key,
        chain_id=plan["chain_id"],
    )
    print(f"\nAgent address: {logger.agent_address}")
    current = logger.contract.functions.agentRiskParams(
        logger.agent_address).call()
    print(f"Current on-chain params: {current}")
    for w in check_tightening(tuple(current), plan):
        print(f"WARNING: {w}", file=sys.stderr)

    tx = logger.set_risk_params(
        max_position_usd=plan["max_position_usd"],
        max_daily_loss_usd=plan["max_daily_loss_usd"],
        max_leverage_bps=plan["max_leverage_bps"],
        min_confidence_bps=plan["min_confidence_bps"],
    )
    print(f"Risk params tx: {tx}")
    for asset, usd in plan["asset_caps"]:
        cap_tx = logger.set_asset_cap(asset, usd)
        print(f"Asset cap {asset} tx: {cap_tx}")

    updated = logger.contract.functions.agentRiskParams(
        logger.agent_address).call()
    print(f"On-chain params after: {updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
