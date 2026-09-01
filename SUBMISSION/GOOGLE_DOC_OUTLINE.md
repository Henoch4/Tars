# Google Doc Outline — T3N Trusted Enterprise Agent Submission

> Copy into a public Google Doc, link it in the Superteam Earn submission
> form alongside the public GitHub repo. Public sharing: "Anyone with the
> link — Commenter" or "Viewer".

---

## Title
TARS — a trusted enterprise trading agent with an enclave-enforced risk gate (T3N)

## 1. One-paragraph pitch
TARS is an autonomous trading agent where the *entire risk policy runs inside
a T3N TEE contract*. The agent cannot trade anything the enclave has not
approved, and every decision — approvals, rejections and executions alike — is
written to an immutable per-tenant KV audit trail before execution. Written
first, traded second: if the write fails, no trade happens. On top of that
core guarantee the contract layers crash-veto, mandate + human escalation,
fractional-Kelly sizing and an in-enclave neural net.

## 2. Why it's useful for enterprises
- Compliance-grade audit trail: reasons for every approve/reject/execute,
  keyed `dec_<epoch>_<seq>_<asset>`, collision-free via host `seq-no`.
- Policy-as-data: risk parameters live in tenant KV (`risk_params`); ops teams
  change limits, mandate, crash threshold without touching or re-registering
  the contract.
- Over-threshold trades raise a **pending human-review escalation** that
  auto-denies after 24h — risk surfacing is built in, not bolted on.
- Time-correct limits: daily loss / trade counters roll over at UTC midnight
  using the host `cluster-timestamp-secs()` — verified, not assumed.
- Policy-only kill switch: empty the allowlist or pause the mandate → the
  enclave halts all trading regardless of client behaviour.
- Real application domain: gates a live OKX-connected service, using real OKX
  historical candles (no synthetic data) for signals and the NN features.

## 3. What was built (contract v0.1.3, ~309 KB WASM, 12 exports)
- `z-trading-risk-gate` — custom WASM TEE contract (Rust → wasm32-wasip2,
  wit-bindgen 0.49):
  - `validate-trade` (allowlist, size, confidence, leverage, daily loss/count),
  - `log-decision`, `get-risk-params`, `get-daily-stats`,
  - `set-mandate` / `get-mandate` / `evaluate-mandate` / `resolve-escalation` /
    `list-escalations` (owner-gated authority + escalation lifecycle),
  - `compute-size` (fractional Kelly + sweet-spot band),
  - `ml-predict` (17→8→1 net, ~153 f32 constants, no ONNX dep),
  - `execute-plan` (atomic mandate + risk-gate + audit in one TEE call).
- TypeScript service on `@terminal3/t3n-sdk`: tenant/agent/user client setup,
  trade orchestrator, OKX client, HTTP API (`/health`, `/risk-params`,
  `/daily-stats`, `/mandate`, `/escalations`, `/trade`, `/demo`).
- 12 Rust unit tests (mandate, sizing, ML) — fully offline.
- Bug findings: BUGS.md (6 findings, 3 new to this challenge, incl. a new
  `kv_store::scan` visibility bug).

## 4. Live on-chain demo (testnet)
Recorded verbatim in `SUBMISSION/verification/LIVE_OUTPUTS.md`:
- Risk params + mandate read.
- `ml-predict`: neutral 0.485 / bullish 0.638 / bearish 0.488.
- `compute-size`: $2500 Kelly-capped; over-sweet-spot → $0.
- `evaluate-mandate`: $700 → **allow**; $2000 → **escalate** (real id).
- `execute-plan`: atomic — correctly **rejected** low-confidence, correctly
  **refused** above-mandate-max.
- Escalation lifecycle: create → list → resolve → list-empty.

## 5. Deliverables checklist
- [x] Public GitHub repo (this repo, `SUBMISSION/` + `t3n/`)
- [x] Agent DID + API key claimed (go.terminal3.io/adk-community)
- [x] Quickstart + Walkthrough completed
- [x] Custom TEE contract registered on testnet — contract_id 793, v0.1.3
- [x] Live invocation logs (verification/LIVE_OUTPUTS.md)
- [x] BUGS.md findings
- [x] Screenshots
- [ ] X post tagging @terminal3io (bonus)

## 6. Post-challenge: keep running or hand over?
**We will continue running TARS and are joining Terminal 3's startup program
and listing page.** The agent is structured for long-lived unattended
operation: policy is KV data (no redeploys to change risk limits), the
enclave enforces risk + mandate regardless of client behaviour, over-threshold
trades escalate to a human with a 24h auto-deny, and every decision is
permanently auditable. We'd welcome Terminal 3's support via the startup
program and the listing page for distribution. The public repo is the single
source of truth, so an operational handover to Terminal 3 remains possible
later if preferred.

## 7. Screenshots
Contract registration, KV/map seeding, risk-params + mandate reads, live
function invocations, service output.
