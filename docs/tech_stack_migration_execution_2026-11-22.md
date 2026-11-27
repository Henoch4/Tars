# Tech-stack migration execution report — 2026-11-22

## Scope

This run covers the repository-wide migration boundary without enabling live
execution. Python remains the policy, research, enrichment, paper, and QA
runtime. The future signer/broadcaster remains isolated and unimplemented.

## Completed in this run

- Added the canonical `ExecutionIntent` / `ExecutionReceipt` contract.
- Enforced dry-run defaults and explicit live approval in the contract.
- Added contract tests for invalid amounts, slippage, live combinations,
  broadcast receipts, and sanitized refusals.
- Ran the safety freeze: `SAFETY_FREEZE_PASS`.
- Added pytest configuration so vendored `model-inspiration` projects are not
  accidentally collected as Tarstrade tests.

## Verification

- Contract/control-plane smoke suite: 11 passed.
- Initial Tarstrade suite: 1,643 passed, 5 failed.
- Resolution: FPZ and on-chain-chaos fixtures now provide explicit stop/take
  protection, so they reach the risk/logger stages they are intended to test.
- Resolution: the scheduler test now supplies a writable isolated intent-store
  path instead of using the repository's read-only SQLite artifact.
- Final Tarstrade suite: **1,648 passed, 58 warnings**.
- Warnings are non-blocking deprecations/resource warnings and pytest cache
  permission warnings; no test failures remain.
- Unscoped `pytest` previously collected vendored projects and produced 48
  dependency-collection errors. The new `testpaths` boundary prevents that
  false signal.

## Safety boundary

No wallet, RPC, signer, broadcast, VPS, or live flag was changed. The GTA
service remains disabled and the repository safety scan remains green.

## Execution phase checkpoint

`src/execution/backend.py` now supplies the first backend implementation:
`ReadOnlyExecutionBackend`. It consumes the planner output and always returns
a sanitized refusal before signing or broadcasting. This is the required
execution seam; it is not live execution authorization.

## Migration sequence now in force

1. Stabilize the Python contract and clear the five existing regressions.
2. Build a read-only Rust or TypeScript planner consuming only canonical intent
   JSON; differential-test it against Python reference behavior.
3. Add isolated signing only after planner parity and security review.
4. Add broadcast only behind existing approvals, reconciliation, and breakers.
5. Retire duplicate Python live adapters only after parity evidence.
