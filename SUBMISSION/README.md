# T3N Submission — TARS: Trade Audit & Risk System (Trusted Enterprise Agent)

> **Bounty:** "Try out new docs to build a trusted agent with T3N that we can distribute / host" — Terminal 3 Network (Superteam Earn)

## What this is

**TARS** is an autonomous crypto trading agent whose entire risk policy runs
inside a **T3N TEE contract**. The agent cannot trade anything the enclave has
not approved, and every decision — approvals, rejections and executions alike —
is written to an immutable per-tenant KV audit trail *before* any order is
placed. Written first, traded second: if the write fails, no trade happens.

This submission is structured for the bounty's two core criteria:
**usefulness / ease of maintenance** and **build quality**. The risk policy is
*data, not code*; the safety layer is *not bypassable by the trading client*;
and the audit trail is *complete by construction*.

## Capabilities (live on testnet, contract v0.1.3)

The enclave contract `z-trading-risk-gate` exports **12 functions**:

| Function | Concern |
|---|---|
| `validate-trade` | allowlist, size cap, confidence floor, leverage cap, daily-loss / daily-count limits (also runs the crash veto) |
| `log-decision` | immutable append-exact per-tenant KV audit trail (written first, traded second) |
| `get-risk-params` | operator policy read |
| `get-daily-stats` | host-clock UTC date + P&L / counters |
| `set-mandate` | owner-only authority profile (max size, escalation threshold, 24h expiry) |
| `get-mandate` | read the current mandate |
| `evaluate-mandate` | grantline-style gate → allow / escalate / deny |
| `resolve-escalation` | owner reviews an escalated plan (auto-denies after 24h) |
| `list-escalations` | pending human-review queue |
| `compute-size` | fractional-Kelly sizing with an asymmetric sweet-spot band |
| `ml-predict` | in-enclave 17→8→1 neural net (P(win), ~153 f32 constants, no ONNX dep) |
| `execute-plan` | atomic mandate + risk-gate + audit in one TEE call |

The agent therefore exposes the four governance patterns enterprises actually
ask for, each enforced *inside* the enclave:

1. **CESF-style crash veto** — a `crash_mass` (P of a >20% drawdown, estimated
   from realized vol of real OKX candles) above a threshold vetoes the trade
   before any size/confidence check.
2. **Mandate + human escalation (Grantline)** — the owner sets an authority
   profile; trades above the escalation threshold raise a pending review that
   auto-expires, so no single client can authorise above the mandate.
3. **Fractional-Kelly sizing (Zinger)** — position size is computed in-enclave
   from confidence (optionally blended with the NN P(win)) and clamped to an
   asymmetric sweet-spot band + a Kelly cap.
4. **Atomic execution (Liquid)** — `execute-plan` bundles mandate check +
   risk-gate + audit-log so success is all-or-nothing at the enclave boundary.

## Why it's useful for enterprises

- **Policy-as-data**: risk parameters are tenant-KV (`risk_params`); an
  operator tightens limits with a KV write, not a code change or contract
  re-registration.
- **Enclave-enforced safety for unattended running**: the risk gate + mandate
  cannot be bypassed by the trading client. A policy-only kill switch (empty
  `allowed_assets`, or a paused mandate) halts all trading instantly.
- **Auditability by default**: every approve/reject/execute carries a reason
  string in the immutable `decisions` map; escalation reviews live in the
  `escalations` map. Compliance reads one map — nothing else needed.
- **Time-correct limits**: daily loss / trade counters are host-clock driven
  (`cluster-timestamp-secs`), roll over at UTC midnight, and survive restarts.
- **Real application domain**: it gates a live OKX-connected service, using
  real OKX historical candles (no synthetic data) for signals and features.

## Architecture

```
                    ┌────────────────────────────────────────────┐
   TS service       │         T3N tenant enclave (TEE)           │
  (Node, t3n-sdk)   │   z-trading-risk-gate (wasm32-wasip2)      │
  ┌──────────────┐  │                                            │
  │ signal: MA   │  │  crash veto ── crash_mass vs threshold     │
  │ crossover    │  │  validate-trade ── allowlist, size, conf,  │
  │  (real OKX   │─►│                   lev, daily loss, count   │
  │   candles)   │  │  mandate ── allow / escalate / deny        │
  │ extract17    │  │  compute-size ── fractional Kelly + band   │
  │  features    │  │  ml-predict ── 17→8→1 net, P(win)          │
  │ OKX client   │◄─│  execute-plan ── atomic gate+validate+log  │
  └──────────────┘  │  log-decision ── immutable KV audit        │
                    └────────────────────────────────────────────┘
```

- `t3n/contract/` — the TEE contract. Rust 1.97 → `wasm32-wasip2`,
  `wit-bindgen 0.49`. ~309 KB release WASM (well under the 2 MiB cap). Includes
  12 unit tests (mandate, sizing, ML) that run fully offline on the host target.
- `t3n/service/` — TypeScript harness (`@terminal3/t3n-sdk`): tenant/agent/user
  client setup, HTTP endpoints (`/health`, `/risk-params`, `/daily-stats`,
  `/mandate`, `/escalations`, `/escalations/resolve`, `/trade`, `/demo`), the
  orchestrator, OKX execution client.
- The broader TARS system (Python signal engine, X Layer audit-trail contract,
  risk profiles) lives in the parent repo; the T3N submission unit is the
  enclave contract + service.

## Risk policy (tenant KV `risk_params`, operator-editable)

```json
{
  "max_position_usd": 5000.0,
  "max_daily_loss_usd": 500.0,
  "min_confidence_bps": 7000,
  "max_leverage": 5.0,
  "max_daily_trades": 10,
  "allowed_assets": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP"],
  "crash_veto_threshold_bps": 2000,
  "min_implied_prob": 0.44,
  "max_implied_prob": 0.62,
  "kelly_fraction_cap": 0.25,
  "equity_usd": 10000,
  "avg_reward_risk_ratio": 1.5
}
```

## Mandate (tenant KV `mandate_1`, owner-set via `set-mandate`)

```json
{
  "max_size_usd": 5000.0,
  "min_reserve_usd": 100.0,
  "escalation_threshold_usd": 1000.0,
  "validity_end": 18446744073709551615,
  "paused": false,
  "escalation_timeout_hours": 24
}
```

Trades under the threshold are auto-approved; above the threshold they raise a
pending escalation (auto-denied after 24h) that the owner resolves; above
`max_size_usd` they are refused outright.

## Build & run

```bash
# Contract (Rust -> WASM component)
cd t3n/contract
cargo build --release --target wasm32-wasip2
# artifact: target/wasm32-wasip2/release/z_trading_risk_gate.wasm  (~309 KB)

# Unit tests (offline, host target)
cargo test --target x86_64-pc-windows-msvc

# Verification (binary-level WIT proof)
wasm-tools component wit target/wasm32-wasip2/release/z_trading_risk_gate.wasm

# Service (TypeScript, t3n-sdk)
cd t3n/service
npm install
cp .env.example .env   # T3N_API_KEY, AGENT_KEY, USER_KEY
npx tsx src/register.ts              # register + create maps + seed policy
npx tsx src/demo.ts                  # end-to-end cycle
npx tsx src/verify-new-functions.ts  # exercises all 7 new enclave functions
npx tsx src/verify-escalation-lifecycle.ts
npx tsx src/index.ts                 # HTTP server on :3000
curl -X POST localhost:3000/trade -d '{"assets":["BTC-USDT-SWAP"]}'
```

## Post-challenge operation (handover statement)

**We will continue running TARS and are joining Terminal 3's startup program
and listing page.** The operator (submitting tenant) keeps the agent live:

- **Policy without deploys** — position caps, allowlists, confidence floors,
  leverage, daily limits, mandate and crash-veto threshold are tenant-KV
  values; tightening policy is a KV write, not a code change or re-registration.
- **Enclave-enforced safety** — the risk gate and mandate cannot be bypassed by
  the trading client; a policy-only kill switch (empty allowlist or a paused
  mandate) halts all trading instantly.
- **Escalation without pager duty** — over-threshold trades raise a pending
  review with a 24h auto-deny, so risk surfacing to a human is built in.
- **Auditability by default** — every decision is in the immutable per-tenant
  `decisions` map with a reason string; escalations live in `escalations`.

We are keen on the startup program for support/networking and the listing page
for distribution, and are happy to hand over operational runbooks, onboarding
docs, or the whole agent to Terminal 3 later if that becomes the better path —
the public repo is the single source of truth, so either direction is one step
away.

## Verification

- `verification/z_trading_risk_gate.wit.txt` — `wasm-tools component wit`
  extract of the built component (imports/exports match `wit/world.wit`).
- `verification/LIVE_OUTPUTS.md` — verbatim testnet invocation logs (risk
  params read, mandate read, ml-predict, compute-size, evaluate-mandate
  allow/escalate, execute-plan atomic approve/reject, escalation lifecycle).

## Findings (bugs faced)

See `SUBMISSION/BUGS.md` (6 findings, 3 new to this challenge).

## Screenshots

See `screenshots/` (contract registration, KV/map seeding, live function
invocations, service output).
