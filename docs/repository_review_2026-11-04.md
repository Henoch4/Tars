# Tarstrade repository review — 2026-11-04

## Conclusion

The earlier build plan was too narrowly centered on FPZ routing and order-ID
format. The next milestone should be **one reproducible, paper-only execution
runtime whose controls and lifecycle match the evidence**. Installing more
strategies, integrations, services, or UI frameworks is not the next step.

The repository has substantial implemented machinery, but component tests and
module presence do not establish that the deployed scheduler uses that machinery.
Keep FPZ activation, Book 2 restart, paid service activation, and depositor launch
as separate gates. No VPS state was inspected or changed during this review.

## Scope and limits

The inventory covers 456 files: tracked and nonignored project files plus 26
local-only first-party planning files missed by Git's default inventory. It
includes 81 Python source modules, 97 Python files under `tests/`, 57 files under
`scripts/`, EVM/Solana contracts, static pages, CI/build configuration, manifests,
research reports, and local master plans. All eligible text was read by the
inventory/scans; 250 Python files, including generated modules, were AST-parsed.
Critical execution and operational paths received the detailed source review
described below. This is not a claim that every line of every file received a
manual security audit.

Secrets, wallets, Git internals, caches/dependencies, ignored runtime ledgers and
model weights, vendored submodules, and separate sibling projects were excluded.
Images/PDFs and generated datasets/tokenizers were inventoried rather than
visually or economically validated. Source review of the HTML is not browser QA.
No research conclusions were refreshed against the web or a live venue.

The per-file inventory is local-only at
`data/reviews/repository_review_inventory_2026-11-04.json`. It records hashes,
sizes, structural coverage, and source/test definitions. Local memo contents
remain outside the public review document.

## Confirmed findings that change the order of work

| ID | Finding and evidence | Consequence for the plan |
|---|---|---|
| R01 | `src/scheduler.py:109–145` constructs `RiskGate()` and an agent with only FPZ settings. `src/main.py:805–922` additionally configures risk limits, durable counters, logging, integrity, curator, and multi-leg machinery. An offline scheduler probe with a configured $123 loss limit produced a $500 gate, with counters/logging/integrity/curator/manager absent. | Shared, explicit runtime construction is first. Match safety controls while preserving each entry point's permitted strategy set; do not accidentally enable API-only strategies in the scheduler. |
| R02 | `Settings` accepts `dry_run=False, allow_live=False`; its validator only requires explicit DRY_RUN when ALLOW_LIVE is true. `scripts/preflight_check.py` rejects the inverse combination, but the scheduler does not run that preflight. `/trade` does not enforce ALLOW_LIVE. | Enforce the same two-switch live policy at runtime, not only in an operator script or `/hire`. |
| R03 | In directional, package, and consensus paths, onchain logging is conditional on a logger being present. At `src/agent.py:1743–1787`, an absent logger selects the branch labeled dry-run, but execution still uses the actual executor's mode. `require_onchain_sync` exists but defaults false and is not enabled by the API factory. | Fail live startup/dispatch when the mandatory audit authority is missing or unreconciled. Resolve the conflict between the master plan's onchain requirement and `.env.example`'s claim that logging was removed. Do not silently select a weaker policy. |
| R04 | `DurableDailyCounters` holds a per-instance snapshot, with thread locks rather than process transactions (`src/execution/risk_gate.py:52–176`). A probe with two instances writing one increment each left one increment on disk. Corrupt JSON returned empty state; write errors alert but do not fail the mutation. | Establish a single execution owner and transactional risk state. Corruption, unavailable storage, and uncertain ownership must block new entries. Merely setting `counters_durable=True` is insufficient. |
| R05 | `src/agent.py:1465` appends funding every asset cycle. The paper harness deliberately deduplicates by funding-period identity (`scripts/paper_trade_sol.py:162–181`). Five same-period agent updates produced five history points. History is in-memory in the agent. | Verify paper/agent sampling parity before considering cohort evidence transferable. Carry period IDs, settled rates, and history recovery through the market-data interface. |
| R06 | The paper harness has opposite-signal and 21-period exits, entry break-even checks, and funding accrual. The directional agent path constructs entries and skips same-direction positions; its `_open_positions` is only initialized, and the opposite-position guard prevents an unmarked reversal. There is no corresponding persisted FPZ exit/hold/funding lifecycle in that path. | FPZ routing is not a complete trading implementation. Specify and test entry → hold → reduce-only close → realized accounting → restart recovery, against the locked paper contract. |
| R07 | SafetyNet compares rate returns with dollars. In addition, `src/agent.py:1629–1631` catches SafetyNet exceptions and continues without its size cap. The agent passes a synthetic book proxy, and returned stop/take values are not installed as exits. | Fix dimensions, error behavior, capital source, and stop semantics as one explicit contract. Do not simply multiply a rate by capital or fix only the happy path. |
| R08 | `config/profiles.yaml` declares defensive `enabled_signals: []`; `src/agent.py:436` turns an empty list into `None`, meaning unrestricted filtering. Reproduced offline. | Treat defensive profile semantics as a distinct safety fix, before any new runtime construction starts using it. Empty and absent must have different meanings. |
| R09 | `src/agent.py:1170–1171` calls `settle()` immediately after package entry reaches LOCKED. `settle()` releases tracking without closing venue positions. Recovery labels LOCKED terminal and deletes its scratch file; `_reconcile_package` explicitly trusts saved state rather than querying the venue. A restart probe changed one tracked LOCKED package to zero tracked packages and deleted its state file. Unwind/close cleanup also needs remaining-exposure verification. | Keep the unvalidated delta-neutral package disabled. Require venue-backed recovery and a funded-position lifecycle before any package rollout. Do not spend the next sprint expanding a thesis already reported as failing costs. |
| R10 | `src/execution/executor.py` treats a failed position read as no position. Market snapshots use fetch-completion timestamps, and funding failures can become a numerical zero (`src/agent.py:297–339`). | Distinguish unavailable, flat, and zero-rate data. Validate venue event time, nonfinite/future values, and period identity; do not let a failed read disable exposure checks. |
| R11 | The signal-ID change fixes format only. Default, consensus, and package producers still generate labeled IDs. A new UUID per reconstructed signal does not provide durable intent deduplication after a submission timeout. | Finish venue ID contracts together with an order-intent journal and lookup-before-retry semantics. Preserve historical IDs; never normalize only at dispatch. |

## Paper evidence and accounting

1. The October 7 next-bar repricing is already recorded in the local master plan
   and `reports/validation-gate-2026-10-07.md`: SOL FPZ net +2.6%, OOS Sharpe
   1.505, OOS Calmar 6.522, 28 trades, negative IS Sharpe. Older +13.9% / 2.206 /
   16.125 numbers must be labeled historical rather than reused as current proof.
   These are historical research results, not a current forward verdict.
2. Historical documents disagree about whether the edge is directional or a
   delta-neutral package. The standalone paper harness and FPZ wiring implement
   directional exposure. Book 1's depositor thesis is delta-neutral. A SOL FPZ
   paper PASS does not authorize marketing that exposure as hedged depositor carry.
3. The paper runner says it stops for kill conditions, but its final printed PASS
   checks trade count and positive net only. No explicit kill-switch integration
   or persisted kill-anomaly verdict is present in that runner. Make the external
   operational evidence required for "no kill anomalies" explicit; never infer it
   from the absence of recorded events.
4. Paper state uses direct writes and resets to empty on parse failure. Missed
   cycles, funding settlements during suspension, restart state, and ledger/state
   reconciliation need evidence-quality checks. Instrumentation changes must be
   versioned without rewriting a cohort or tuning its parameters.
5. `src/reconciliation.py:165` suggests NAV from the USDT equity component rather
   than complete scoped equity plus vault reserves. Account ownership, transfers,
   unrealized PnL, reserves, and Book 1/Book 2 separation need one accounting
   definition. The cycle's equity-decline loss feed is not automatically realized
   strategy loss and is sensitive to deposits/withdrawals and restart baselines.
6. The agent estimates profit from funding/notional, including a placeholder
   one-basis-point directional sweep. `wallet.py` is a demo custody abstraction;
   its cold confirmation is a returned dictionary, not a hardware-wallet proof.
   These must not become realized-profit or custody-completion claims.
7. `TradingCycleResult.total_pnl_usd` and `total_fees_usd` are initialized to zero
   but never accumulated in the agent; `/trade` returns those zeros. The agent
   also never calls `CuratorAgent.record_trade_pnl`. The curator's tested
   underperformance reversion therefore has no realized-trade feedback from this
   pipeline. Close accounting must feed both reported totals and curator evidence,
   with units and observation timestamps, rather than manufacturing profit.

## Operational and product follow-ups

- **Deployment closure:** the handoff already records missing deployed modules.
  Build an artifact from a complete dependency/import closure and verify that exact
  artifact, instead of deploying an expanding handpicked file list.
- **Fresh-host reproducibility:** `pyproject.toml` requires Python 3.14, the VPS
  script installs 3.12, and the manual trading workflow uses 3.11 plus editable
  installation. Reconcile the supported runtime and dependency inputs. The
  Hardhat config also lacks the via-IR setting used by the established solc and
  Foundry compilation paths; verify the documented build command before claiming
  it is equivalent. No compilation or deployment was attempted here.
- **Systemd/proxy correctness:** the deploy script gives services an environment
  file generated with root-only permissions; service-manager env loading and
  application-side file reads must be checked separately. Risk/audit paths are
  not included in that generated template. Nginx's WebSocket route still points
  at port 8001 although the hub is inside uvicorn on 8000. Its application route
  coverage also needs a fresh-host smoke test. Do not infer deployed state from
  this script: the running VPS may have manual repairs.
- **Operator status:** risk stats and kill controls refer to the API's agent;
  scheduler state is a separate process. Heartbeat metrics can prefer API memory
  over a newer scheduler file. Operator QA must prove the displayed controls act
  on the execution owner, rather than merely showing a healthy HTTP process.
- **Feature inventory:** governance mutation/release modules, advisory, research
  delivery, MCP, Solana programs, ML artifacts, and alternative venues have
  different levels of wiring. Record implemented / wired / paper-tested /
  deployed / live-authorized independently. Do not activate them to make an
  inventory look complete. Injective's dispatcher currently supports market data
  rather than the full executor command surface.
- **Payments:** the Moove redemption set is process memory. Persistent hosting
  does not make it survive restarts or coordinate workers. Before charging,
  require atomic durable redemption plus amount/currency/purpose/beneficiary
  validation and failure/retry semantics. Keep payments independent of trading
  strategy proof and disabled unless separately authorized.
- **Depositor contracts:** reserve-aware NAV, settlement-window updates, attested
  NAV fairness after losses, and deposit → trade → close → withdrawal evidence
  precede public funds. `setFundingPackageOpen` exists in the contract but no
  Python caller was found. Treasury and Solana vault prototypes remain separate
  audit scopes; the existing Python suite is not certification of those systems.
- **Documentation authority:** repair `docs/ROADMAP_INDEX.md` relative links;
  several targets exist at the root, while others are absent. Preserve local-only
  plans and internal research. Use current code/evidence for status, not old
  percentages, provisioning checkboxes, test counts, or obsolete restart dates.

## Verification performed

- Per-file inventory, source/test AST extraction, documentation link checking,
  route/configuration/call-site scans, and detailed review of the paths cited.
- Offline probes reproduced scheduler construction drift, missing inverse live
  interlock, counter overwrite, corrupt-state reset, defensive allowlist inversion,
  per-poll funding samples, and loss of LOCKED package tracking after restart.
  Two additional branch probes confirmed that a missing logger, including after
  an injected SafetyNet exception, still reached a fake dispatcher in live mode
  while labeling the decision dry-run. The economic gate was stubbed for the
  first probe; these demonstrate control flow, not actual venue eligibility.
  Probes used fake clients and temporary directories; no venue call or chain
  transaction was made.
- `python -m mypy src --no-incremental`: **Success, no issues in 81 source files**.
- Fresh full regression: `python -m pytest tests/ -q -p no:cacheprovider`, with
  process-local paper-mode/external-configuration isolation: **1410 passed,
  56 dependency warnings in 125.59 seconds**. This includes the 18 identity tests.
  These passing regressions do not establish coverage of the findings above.
- No production source, existing tests, trading parameters, service state,
  credentials, or external application state were changed in this review.

## Recommended next implementation

Start with an explicit runtime builder and contract tests comparing the API and
scheduler's safety configuration, plus the runtime live interlock and mandatory
audit policy. Preserve paper behavior and strategy scope. Then address execution
ownership and durable state before sampling parity, SafetyNet, and FPZ exits.
Write the regressions as new tests; do not weaken existing tests or the validation
bar to make the new implementation pass.
