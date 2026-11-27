# Task-tray evidence control plane

The task tray is the coordination layer for the entire repository. It is
append-only and records task identity, state transitions, dependencies,
evidence artifacts, blockers, ownership, and scoped operator approvals.

## Required lifecycle

`queued → blocked/ready → running → review → complete`

Every promotion to `review` must attach evidence. Any task with live impact
requires a separate scoped operator approval event; a calendar window or a
passing test cannot substitute for that approval.

## First seeded work areas

- Safety freeze
- Canonical strategy lifecycle
- Durable intent/accounting ledger
- Unified data and experiment schema
- Main-satellite analytics
- Runtime/API/scheduler reconciliation
- Provider and restart failure-injection QA
- Deployment artifact verification

The tray does not start services or broadcast orders. It records what is
allowed to happen and why, while existing runtime gates remain authoritative.
