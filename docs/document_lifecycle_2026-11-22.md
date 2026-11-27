# Tarstrade document lifecycle and implementation status

This file prevents research, historical reports, and active implementation
contracts from being confused with one another.

## Current authority

- `docs/build_plan_2026-11-04.md` — current build sequence and gates.
- `docs/repository_review_2026-11-22.md` — current architecture and QA review.
- `docs/task_tray_control_plane.md` — work-item state and evidence rules.
- `docs/ROADMAP_INDEX.md` — navigation only; it does not authorize trading.
- `LIVE_TRADING_PLAYBOOK.md` — operator decisions and safety doctrine.

## Implemented and verified

- Execution intent/receipt boundary: `src/execution/intent.py`.
- Durable intent, ownership, reconciliation, and position-reader modules.
- Task tray control plane and safety-freeze check.
- GTA enrichment, reconciliation, exits, flattening, and synthetic replay tests.
- Python test suite: 1,648 passing on 2026-11-22.

## Active but gated

- Main memecoin satellite: paper/data collection only.
- FPZ: default-off and cohort-gated.
- GTA: historical case study/data-report path only. GTA live modules are frozen
  and are never promoted or authorized by the active roadmap.
- Age variant: paper experiment only; never promoted automatically.
- Rust/TypeScript execution migration: contract exists; planner not yet live.

## Scope correction — GTA is analytics-only

The GTA event window is closed as a trading objective. Its code and reports
remain useful for historical analysis, enrichment research, and QA fixtures,
but no GTA service, shadow, wallet, or live promotion is part of the active
Tarstrade build. New work must prioritize the core runtime, paper satellites,
experiment framework, analytics, QA, and deployment reliability.

## Proposed, not implemented

The following documents describe ideas or future work. They do not represent
working features until a task-tray item, code, tests, and evidence artifact
exist:

- `docs/*stealables*.md` and inspiration documents.
- `docs/alpha-mode-memecoins.md`.
- `docs/base_to_xlayer_cctp_feasibility.md`.
- `docs/pons_robinhood_chain_research.md`.
- `docs/hyperliquid_slippage_model.md`.
- `docs/Marketing Strategy.pdf` and product/market proposals.

## Historical evidence — retain, do not execute

The following are preserved for audit but should not be used as current
instructions: dated repository reviews, old restart plans, `PATH_FORWARD.md`,
old handoffs, `docs/archive/**`, and dated GTA/validation reports.

## Deletion policy

Do not permanently delete dated evidence, calibration outputs, or old runbooks
until the related audit/retention period is explicitly closed. Remove only
duplicate active copies or secrets; archive historical material instead.
