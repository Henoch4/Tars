# WASM WIT verification — `wasm-tools component wit`

## Status: complete

The release component is built:
`t3n/contract/target/wasm32-wasip2/release/z_trading_risk_gate.wasm`
(**308,953 bytes** — comfortably under the 1 MiB `max_wasm_bytes` cap; the
contract imports `kv-store`, `logging` and `tenant-context` only, pruning the
unused `http` host interfaces).

The binary-level WIT extract is included as `z_trading_risk_gate.wit.txt`
(generated via `wasm-tools component wit`).

## Expected result

Imports (must match `t3n/contract/wit/world.wit`):
- `host:tenant/tenant-context@1.0.0` (incl. `cluster-timestamp-secs`, `seq-no`)
- `host:interfaces/logging@2.1.0`
- `host:interfaces/kv-store@2.1.0`

Exports (`z:trading-risk-gate/contracts@0.1.3`) — 12 functions:
- `validate-trade`, `log-decision`, `get-risk-params`, `get-daily-stats`
- `set-mandate`, `get-mandate`, `evaluate-mandate`, `resolve-escalation`,
  `list-escalations`
- `compute-size`, `ml-predict`, `execute-plan`

Registration acceptance on-cluster (contract_id **793**, printed by
`t3n/service/src/register.ts`) corroborates the same result, and every export
was exercised live via `T3nClient.executeAndDecode` (see `LIVE_OUTPUTS.md`).

## Already proven (real, not templated)

- `cargo test` (host target): 12 unit tests pass (mandate ×5, sizing ×4,
  ml ×2, +1 doc-test) — run fully offline.
- `cargo build --release --target wasm32-wasip2`: clean, 308,953 bytes,
  only 2 benign dead-code warnings.
- Live testnet invocations of **all 12 exports** recorded in
  `SUBMISSION/verification/LIVE_OUTPUTS.md` (risk/mandate reads, ml-predict,
  compute-size, evaluate-mandate allow/escalate, execute-plan atomic approve/
  reject, escalation lifecycle).
