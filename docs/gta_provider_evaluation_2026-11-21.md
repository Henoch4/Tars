# Stateful sellability provider evaluation — 2026-11-21

Rewind (Solana Vibe Station) is the first provider found whose documented
surface matches the required architecture: historical account reads, coherent
multi-account positions, and `simulateHistoricalTransaction` against archived
state, feature set, sysvars, lookup tables, and program bytes. It also exposes
the simulation response and resolved historical position. It must still pass
our own calibration; documentation alone is not acceptance.

Acceptance plan:

1. Provision a Rewind key only through the VPS secret manager.
2. Capture one unsigned buy and one unsigned sell artifact.
3. Anchor both to the same finalized transaction/slot position.
4. Require explicit `sellable`/`not_sellable`, slot, conditions, reason, and
   freshness.
5. Compare with Token-2022 inspection and the synthetic replay; disagreement
   becomes `unknown`/conflict.
6. Run two-candidate calibration before any sixth-shadow decision.

No key has been provisioned and no provider request was made. Live flags and
the sixth shadow remain disabled.
