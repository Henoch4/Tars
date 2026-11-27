# Tarstrade technology-stack migration map — 2026-11-22

## Scan result

- Python: 186 files across `src` and `scripts`
- TypeScript/JavaScript: none
- Rust: none
- Current UI: Python-served/static web surface

This is not a reason to rewrite the repository. Python is appropriate for the
research, orchestration, paper, and data layers. The scan identifies only the
execution boundary for language migration.

## Keep in Python

- `src/signals.py`, `src/cost_model.py`, research/model code
- discovery, enrichment, provider adapters, and experiment harnesses
- paperz and paper executors
- task tray, scheduler, reporting, and QA orchestration
- OKX/Injective/Hyperliquid research and API adapters
- FastAPI dashboard/control plane

## Isolate before any live authorization

These modules contain or influence signing, transaction construction, or
broadcast behavior and should move behind an isolated execution service:

- `src/meme_execute.py`
- `src/meme_live_adapter.py`
- `src/meme_live_trader.py`
- `src/gta_live_executor.py`
- `src/gta_live_runner.py`
- `src/signer.py`
- `src/solana/meteora_executor.py`
- `scripts/transfer_gta_reserve.py`
- `scripts/meme_live_buy.py`

## Target boundary

1. Python produces a typed, audited `ExecutionIntent`.
2. A Rust or TypeScript service validates the intent and constructs/signs the
   transaction using the official Solana SDK.
3. The service returns a signed/broadcast receipt or a typed refusal.
4. Python remains the policy owner and never receives private keys.

Preferred execution implementation: Rust for the signer/broadcaster, with
TypeScript `@solana/kit` acceptable for transaction planning and operator UI.
Python/Solders remains valid for read-only inspection and the reference test
implementation.

## Migration order

1. Freeze current Python execution behavior with contract fixtures.
2. Define `ExecutionIntent`, `ExecutionReceipt`, and refusal schemas.
3. Build a Rust/TypeScript read-only transaction planner.
4. Differential-test it against Python/Solders.
5. Add signing only in an isolated process with no strategy imports.
6. Add broadcast behind existing approvals and breakers.
7. Retire duplicate Python live adapters only after parity evidence.

## Migration checkpoint — 2026-11-22

The first boundary is now implemented in `src/execution/intent.py`:

- `ExecutionIntent` is the policy-owned, serializable request contract.
- `ExecutionReceipt` is the typed result/refusal contract.
- Live intents require `allow_live=false` by default, non-dry-run mode, and
  explicit operator approval.
- The contract contains no wallet, RPC, signing, or broadcast dependencies.
- Five contract tests cover defaults, invalid amounts/slippage, live approval,
  and refusal/broadcast invariants.

This is a reference boundary, not a live authorization. The next migration
step is a read-only Rust or TypeScript planner that accepts only the canonical
intent JSON and produces a deterministic transaction plan. Signing and
broadcast remain out of scope until differential parity and security review
are complete.

No live migration is authorized by this map. The current default remains
`DRY_RUN=true` and `ALLOW_LIVE=false`.
