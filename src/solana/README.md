# Tarstrade Solana sidecar (ported from Stockulus)

Ported 2026-09-26. Hackathon-expired Stockulus -> future-facing Tarstrade modules.

## Modules
- `bsm.py` - Merton cost-of-carry, BSM pricing, Greeks, hedge bands
- `stock_carry.py` - tokenized-stock carry signal + 90/10 allocation + mispricing scanner
- `sol_signals.py` - Signal dataclass + ensemble (renamed to avoid clash with `src/signals.py`)
- `regime_hmm.py` - 3-state HMM + rule fallback + DBC curve map
- `xstocks.py` - xStocks API (AAPLx/TSLAx/NVDAx), Solana mints, div-yield proxy, blackouts, PoR
- `pyth.py`, `yahoo.py` - price feeds
- `audit_logger_sol.py` - Anchor TradeAuditTrail logger (log BEFORE execute)
- `vault_client_sol.py` - TradingVault client (deposit/attest/withdraw)
- `meteora_executor.py` - Meteora DBC via node subprocess (`ts_reference/` for config)

## On-chain
- `../programs_sol/trade_audit_trail/` - Anchor audit trail (devnet `516a5KdU...`)
- `../programs_sol/trading_vault/` - Anchor vault (`Gd7Ciu6K...`)
Redeploy with new program IDs before mainnet use.

## Docs
- `../docs/solana/EQUATIONS.md`, `BACKTEST.md`, `dbc_configs.sol.json`, `profiles.sol.yaml`

## Use
```python
from src.solana.stock_carry import tokenized_stock_carry_signal, vault_9010_allocation
from src.solana.regime_hmm import infer_regime_simple, dbc_action_for_regime
```
Tarstrade RiskGate remains the gate; Solana loggers are second-rail (EVM default).
