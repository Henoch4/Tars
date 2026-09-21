# Meteora DBC Integration Report — 2026-09-21

## 1. Assignment

Read the Tarstrade codebase end-to-end, state its overarching goal, identify
what gaps it fills relative to the Superteam Earn bounty **"Best use of
Meteora's Dynamic Bonding Curve (DBC)"** (Colosseum Crypto World's Fair side
track, $20k USDC), and implement the integration.

Bounty judging criteria (from the listing): depth of Meteora integration,
technical execution, originality/taste, impact potential, traction/volume.

## 2. Tarstrade overarching goal (from reading the repo)

**TARS = Trade Audit & Risk System** — an autonomous crypto carry agent
(`README.md`, `HACKATHON_SUBMISSION.md`) where the strategy layer may be
creative but the risk layer is boring, deterministic, and non-overridable.

Pipeline (`src/agent.py`, `src/main.py`):

```
Market Data → Signals → RiskGate → Onchain log → Execution (OKX CLI)
```

- **Non-overridable RiskGate** (`src/execution/risk_gate.py`): position/daily
  loss/trade-count/volume caps, kill switch, confidence floor, fat-finger and
  slippage collars, reduce-only, price-freshness gate.
- **Audit-before-trade**: every decision is signed (EIP-191) and logged to
  `TradeAuditTrail.sol` on X Layer (chain 1952) *before* the order is placed;
  if logging fails, the trade is blocked (`src/audit_logger.py`).
- **Ported governance**: curator profile selector (`src/curator.py`,
  `config/profiles.yaml`), pre-signal data-integrity gate
  (`src/data_integrity.py`), atomic multi-leg execution with unwind-on-partial
  (`src/multi_leg.py`), walk-forward/PBO/Calmar validation gate
  (`src/validation.py`), append-only JSONL audit log (`src/audit_trail.py`).
- Live thesis is delta-neutral funding carry (long-spot / short-perp packages);
  `EXPANSION_ROADMAP.md` sequences alts → memecoin satellite (personal book
  only) → tokenized stocks, and explicitly notes memecoin legs need DEX /
  bonding-curve-aware execution venues in future.

## 3. Gaps identified

Against the bounty, TARS had **zero Meteora surface**:

1. No bonding-curve vocabulary anywhere (grep for meteora/dbc/damm: no hits
   outside an unrelated tokenizer blob).
2. No DBC-aware signal — signals were mean-reversion, momentum, funding-rate,
   funding-persistence-z, ML carry.
3. No per-leg curve/fee/graduation config in the multi-leg state machine and
   no crash-recovery persistence for such fields.
4. No curator profiles selecting launch configurations (the bounty explicitly
   lists a "DBC Config Preset Marketplace" idea).
5. No env/settings wiring for DBC defaults.

## 4. Implementation (what was changed)

All changes reuse the existing risk spine — no second order path was invented.

| File | Change |
|---|---|
| `src/execution/risk_gate.py` | New `DBCCurveType` string-constant class (`flat`/`exponential`/`long`/`custom`) + `DBCCurveConfig` dataclass. `RiskGate.__init__` takes `dbc_curve_type/fee_bps/graduation`, validated fail-closed (bad curve, fee outside 0–10000 bps, graduation outside (0, 1] raise `ValueError`). |
| `src/execution/__init__.py` | Re-exports `DBCCurveType`, `DBCCurveConfig`. |
| `src/signals.py` | New `dbc_curve_signal()`: funding-extremity signal weighted by curve shape (exponential 1.2×, long 1.1×, flat 1.0×, custom 0.9×, capped at 95%). |
| `src/multi_leg.py` | `Step` carries `dbc_curve/fee_bps/graduation` (JSON-serializable primitives); `validate_steps` rejects invalid curves/fees/graduations; `_save_package`/`_load_package` and `LegResult.to_dict`/`from_dict` round-trip the fields (`.get()` defaults keep old persisted files loadable). `inverse()` preserves DBC fields on unwind legs. |
| `config/profiles.yaml` | Three new allowlisted profiles: `dbc_flat` (fee 50 bps, graduation 0.5), `dbc_exponential` (75 bps, 0.3), `dbc_long` (100 bps, 0.7), each with per-cohort consensus thresholds. `default_profile` unchanged (`standard`). |
| `src/curator.py` | New `dbc_curve_type()` accessor; `consensus_settings()` now also returns `dbc_curve_type/fee_bps/graduation`. |
| `src/agent.py` | Cycle knob defaults (`_dbc_*`); `_resolve_curator_profile` resolves DBC knobs (profile default, `DBC_*` env override wins); `_generate_signals` emits `dbc_curve` **only** when the active profile allowlists it — default behavior for existing profiles/tests is unchanged. |
| `src/settings.py` | New knobs `dbc_curve_type="flat"`, `dbc_fee_bps=100`, `dbc_graduation=0.5`. |
| `src/main.py` | `_make_risk_gate` passes the three DBC settings into `RiskGate`. |
| `.env.example` | Documents `DBC_CURVE_TYPE/FEE_BPS/GRADUATION` (no secrets). |

## 5. Verification

- `C:\Python314\python.exe -m pytest tests/ -q --basetemp=<tmp> -p no:cacheprovider` → **612 passed**, 43 warnings (all pre-existing deprecation warnings).
- Focused runs: `test_multi_leg.py` + `test_signals.py` + `test_curator.py` → 55 passed; `test_execution.py` + `test_agent_wiring.py` → 93 passed (7 `tmp_path` fixture errors on the default temp dir were environmental — stale locked `pytest-of-Henoch` dir — and clear with a fresh `--basetemp`).
- `git status`: only the 10 intended files modified; no `.env`, no secrets, no keys.
- Regression discipline preserved: slippage enforcement in the dispatch path untouched; `PaperFillSimulator` still never clamps slippage (the two ported-bug invariants from `AGENTS.md`).

## 6. Deliberately NOT done (next milestones)

- **No on-chain Meteora calls yet.** Nothing shells out to the DBC/DAMM-v2
  Solana programs or the `dynamic-bonding-curve-sdk` / `damm-v2-sdk`. The
  `Step` → `LiveFillSimulator` contract is where that execution leg plugs in;
  the governance (presets, validation, auditability) is now ready for it.
- No `/api/v1/dbc-*` HTTP surface, no DBC graduation detector, no DAMM-v2
  migration package path — all natural follow-ups on top of this spine.
- No new tests were added for `dbc_curve_signal` / DBC validation; existing
  suite guards against regressions but a dedicated `tests/test_dbc.py`
  (signal math per curve, invalid-curve rejection, persistence round-trip,
  allowlist gating) should be the next commit.

## 7. Environment notes

- `anchorpy` was uninstalled to fix a broken pytest plugin
  (`anchorpy.pytest_plugin` → `ModuleNotFoundError: pytest_xprocess`); it is
  unrelated to this repo's tests.
- Pre-existing working-tree state left untouched: uncommitted Panta/swarm
  hunks in `src/agent.py` (e.g. `_panta_phase`) and untracked
  `scripts/asp_handoff.bat`, `scripts/vercel_relink.bat`,
  `src/panta_client.py`.
