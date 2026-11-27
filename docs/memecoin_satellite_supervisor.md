# Main memecoin satellite supervisor

The main satellite now has a dedicated control-plane abstraction in
`src/meme_supervisor.py`. It is intentionally separate from strategy and
execution code.

## Guarantees

- `enabled: false` performs no discovery, reconciliation, or execution.
- Shadow mode performs discovery and exit observation but never calls the entry
  handler, even if a candidate passes.
- Live entry handlers are callable only when both `enabled` and `live_enabled`
  are true.
- Reconciliation is required on startup and at the configured cadence. A
  mismatch, missing reader, malformed response, or exception halts new work.
- State is written atomically and every lifecycle event is appended as JSONL.
- Emergency stop attempts the injected flatten operation before halting.

## Remaining integration work

The supervisor is the control-plane foundation, not a claim that live trading
is enabled. The next integration slice must connect the existing main-satellite
screening, exact token-balance reader, live exit accounting, and flatten path
through these injected handlers. That integration stays disabled until it has
its own shadow run and smallest-size canary.

`src/meme_shadow_runner.py` now supplies the missing read-only observation
loop. It records every candidate, enrichment failure, filter verdict, and
round summary to JSONL. `deploy/tars-meme-shadow.service` is a disabled
deployment artifact only; it has not been installed or enabled on the VPS.

`src/meme_live_adapter.py` adds exact raw-unit reconciliation for the current
single-position state. A mismatch or read failure returns `ok: false`; it does
not sign, broadcast, or mutate positions.

`src/meme_exit_adapter.py` is the matching exit primitive: it sizes from the
latest raw balance, requires an explicit confirmation string, verifies the
post-fill balance, preserves residual dust, and reports unresolved exposure
during flattening.

`src/meme_satellite_runtime.py` now composes these pieces into one supervisor
factory. The composed runtime reconciles before screening, keeps entry and
sell handlers inert without confirmations, and routes emergency stops through
the flatten adapter.
