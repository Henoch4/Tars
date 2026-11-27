# Tarstrade Repository Review — 2026-11-22

Reviewed 600 tracked files: 52 docs, 120 source files, and 143 test files.

## Canonical findings

- `docs/build_plan_2026-11-04.md` plus current code/evidence should be the authority.
- `TODO.md`, old restart runbooks, and archived handoffs are historical references,
  not current status authorities.
- The repository contains overlapping core, memecoin, GTA, paperz, and venue
  runners; they need one shared Strategy/Satellite lifecycle interface.
- Future propositions must enter through one experiment framework; falsified
  directional ML/mean-reversion/momentum proposals stay closed.
- GTA is historical case-study material, not an active strategy.
- GTA live/event work is frozen; only data reporting and QA fixtures remain in
  scope.

## P0 corrections

1. CI live interlock fixed: `ALLOW_LIVE=false` in `trading-loop.yml`.
2. Add the append-only `TaskTray` control plane linking hypotheses, dependencies,
   evidence, operator approvals, blockers, and exact next actions.
3. Reconcile stale timers/timezones and replace fixed-date scheduling with
   readiness-aware orchestration.

## P1 build order

1. Durable task tray and evidence manifest.
2. Canonical strategy lifecycle interface.
3. Unified data/experiment schema.
4. Main-satellite short-window analytics.
5. Restart/failure-injection QA and provider outage tests.
6. Runtime/API/scheduler wiring audit.
7. Deployment artifact and fresh-host smoke verification.

All live paths remain disabled until their evidence and approval gates pass.
