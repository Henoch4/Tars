# TARS + RPC Fast Infrastructure Sidetrack: Gap Analysis & Integration Report

**Project:** TARS — Trade Audit & Risk System (Tarstrade repo)
**Bounty:** Colosseum Crypto World's Fair Hackathon | RPC Fast Infrastructure Sidetrack
**Sponsor:** RPC Fast
**Report date:** 2026-09-21
**Deadline:** 2026-11-11 (sponsor-scheduled)

---

## 1. Tarstrade overarching goal

TARS is an autonomous multi-agent crypto trading system with a non-overridable risk layer and a write-first audit trail.

Pipeline (`README.md:18-26`, `HACKATHON_SUBMISSION.md:30-48`):

```text
Market Data → Signal Engine → Risk Gate → Onchain Logger → Execution
OKX CLI       Mean Reversion   (non-overridable)
              Momentum         if rejected → BLOCKED
              Funding Rate     if approved → logDecision() on X Layer
                                               then executeOrder() via OKX CLI
```

Core guarantees:

1. **Non-overridable risk gate:** `src/execution/risk_gate.py` + `src/execution/executor.py` enforce position, daily loss/trades/volume, leverage, confidence, allowlist, price freshness, slippage collar, reduce-only, kill switch. The AI signal layer cannot bypass it.
2. **Write-first onchain audit:** `src/audit_logger.py:179` (`OnchainLogger`) signs EIP-191 payloads and calls `logDecision()` on `contracts/contracts/TradeAuditTrail.sol:166` on X Layer **before** execution. If logging fails, no trade happens.
3. **Ported governance:** `src/data_integrity.py` (Phase 1.5 pre-signal gate), `src/curator.py` (`config/profiles.yaml` allowlist), `src/multi_leg.py` (atomic funding-arb packages), `src/validation.py` (walk-forward/PBO/Calmar gate), `src/audit_trail.py` (local JSONL log).
4. **FastAPI surface:** `src/main.py:803` (`POST /trade`), `src/main.py:635` (`POST /hire`), `/audit-stats`, `/risk-stats`, `/kill-switch/*`, `/api/v1/validation`, `/api/v1/curator-profile`, x402 paywall (guarded import, `src/main.py:32-44`).
5. **Chain:** X Layer (EVM, chainId 1952). Config via `src/settings.py:147-154` (`xlayer_rpc_url`, `audit_contract_address`, `agent_wallet_private_key`).
6. **Market/execution venue:** OKX via `src/okx_cli.py` (`OkxCli` shells out to global `okx` binary) and `src/exchange.py`. Market fetch path is `src/agent.py:240` (`_fetch_market_data`: `market trades` + `market funding-rate` + position).

Tests are fully offline (`OkxCli` monkeypatched): `python -m pytest tests/ -q`.

What Tarstrade **already fills well**:

- Trustworthy autonomous trading narrative (risk cannot be overridden).
- Verifiable decision log (EIP-191 + `TradeAuditTrail.sol` + local JSONL seal).
- Multi-strategy signals (`src/signals.py:130` mean reversion, `:297` momentum, `:454` funding rate, `:522` funding persistence Z, `:1165` ML carry).
- Production hardening: RPC failover (`src/audit_logger.py:281`), durable risk counters, kill-switch both layers, x402/Moove billing guards.

---

## 2. Full RPC Fast bounty details

Source: Superteam Earn listing pasted by user (“Colosseum Crypto World’s Fair Hackathon | RPC Fast Infrastructure Sidetrack”).

### 2.1 Sponsor pitch

> RPC Fast provides high-performance Solana mainnet RPC and data streaming infrastructure, from lightweight endpoints for early-stage development to production-grade infrastructure.

Infrastructure hosted in **Frankfurt (FRA)**. **Mainnet endpoints only** (no testnet/devnet per listing).

Offer period for RPC access: **2026-09-15 to 2026-11-15**. Note: this is the access window, not the submission deadline.

### 2.2 Prize pool

- Total: **~$10,500 in RPC infrastructure credits**, across **21 selected teams**.
- Per-team equivalent: **~$500**.
- Form: **subscription plans only**. Not cash, crypto, or monetary transfer.
- Activation: assigned within **2 weeks after official sidetrack results**, starts immediately on assignment.
- No carry-over / reschedule / extension / cash equivalent for unused time or CU credits.
- Abuse / spam / excessive load / ToS violation → suspension/termination (including prize subscriptions).
- Team chooses (subject to fit):

| Option | Value | Best for |
|---|---|---|
| **Aperture – 1 month** | $499 | High-load/trading apps. Shredstream gRPC + optimized TxStream gRPC (decoded shreds) with ALT resolution + optional real-time tx simulation |
| **Stream – 2 months** | $498 | Data-intensive apps, lighter compute, mostly Yellowstone gRPC usage |

Other plans available on request based on project fit.

### 2.3 Eligibility (all required)

1. Submit to **both** Colosseum hackathon **and** RPC Fast Superteam Infrastructure Sidetrack.
2. Project must be eligible under official global hackathon rules.
3. Use RPC Fast infrastructure **during and after** hackathon.
4. Fill application form to claim Focus plan access (up to 2 months; other plans on request).
5. Follow `@rpcfast` on X.
6. Join Telegram + Discord for support/updates.
7. Publish **at least 2–3 posts per month about RPC Fast** for the next 2 months (experience, progress, use case, feedback).

Bonus engagement (considered in selection):

- Quotes/reposts of RPC Fast posts; replies/comments.
- Educational/technical content featuring your build.
- Community interaction across X / Telegram / Discord.

### 2.4 Judging criteria

1. **Project:** concept, originality, execution.
2. **Infrastructure use:** meaningful use of RPC Fast.
3. **Community presence:** consistency and quality of engagement.
4. **Impact:** overall visibility and value generated for RPC Fast community.

Winner announcement by sponsor schedule (listing states Nov 11, 2026 track context).

---

## 3. Gaps: what Tarstrade does NOT cover today

### Gap 0 — Chain mismatch (most load-bearing)

- Tarstrade is **EVM X Layer + OKX CEX**. See `src/settings.py:147-154`, `src/audit_logger.py:179`, `src/agent.py:240`.
- RPC Fast sidetrack is **Solana mainnet RPC/gRPC only** (Frankfurt, no testnet).
- Repo has **no Solana client**: no `solana-py`, no Yellowstone/Shredstream/TxStream consumer, no `RPC_FAST_*` env vars, no Solana address/ALT handling.
- Implication: there is currently **zero “meaningful use of RPC Fast”** possible without adding a Solana data path. This fails judging criterion #2 outright.

Concrete missing pieces:

| Missing | Why it matters for this bounty |
|---|---|
| `RPC_FAST_URL` / `RPC_FAST_GRPC_URL` / API key wiring | Cannot even claim Focus plan access in code |
| Solana market/data fetch (REST + Yellowstone gRPC) | Cannot show Aperture vs Stream fit (trading app → Aperture; data app → Stream) |
| Latency/throughput benchmark (OKX CLI vs RPC Fast) | Cannot evidence “meaningful use” |
| `/api/v1/rpc-fast-status` endpoint | Cannot expose usage stats for posts/submission |
| Shredstream / TxStream / ALT-resolution demo (Aperture) | Cannot justify $499 Aperture pick for a trading app |

### Gap 1 — RPC infrastructure integration

| Area | Current | Needed |
|---|---|---|
| Settings | No `rpc_fast_*` keys in `src/settings.py:96` | Add URL, gRPC URL, API key, plan name, timeout |
| Market data | `src/agent.py:240` only calls OKX CLI | Add Solana path; prefer RPC Fast when configured; OKX as fallback; tag metrics by source |
| Metrics | No `tars_rpc_fast_*` counters | Add usage total, latency histogram, error count, fallback count |
| Docs | `.env.example` has no RPC Fast vars | Document Focus vs Stream choice |

### Gap 2 — Community presence (X / Telegram / Discord)

| Requirement | Current |
|---|---|
| 2–3 posts/month about RPC Fast × 2 months | **0**. No X poster, no template, no scheduler |
| Follow `@rpcfast`, join TG/Discord | Manual only; no checklist/link hub in repo |
| Quotes/reposts/replies, educational content | No tracking, no draft posts, no content calendar |

No code exists for this. `src/alerting.py` is webhook alerting, not social posting. `static/landing.html` + `/dashboard` exist but do not surface RPC stats.

### Gap 3 — Superteam Earn / submission plumbing

| Requirement | Current |
|---|---|
| Dual submission (Colosseum + RPC Fast sidetrack) | Only `HACKATHON_SUBMISSION.md` for OKX Build X AI; no Colosseum/RPC Fast submission doc |
| Application form for Focus plan | No link, no app ID tracking, no env for plan assignment |
| Profile/bounty mapping | No Superteam profile endpoint, no category mapping |
| Grant/bounty API | No `superteam_*` settings or endpoints |

### Gap 4 — Judging-criteria evidence

| Criterion | Tarstrade strength | Missing evidence |
|---|---|---|
| Project concept/originality/execution | Strong: write-first audit + non-overridable gate | Needs Solana-angle framing (why a CEX+X Layer bot also consumes Solana infra) |
| Infrastructure use | None | Benchmarks, gRPC stream demo, `/rpc-fast-status` output |
| Community presence | None | Post calendar, post drafts, engagement log |
| Impact | Indirect (transparency) | Projected RPC credit utilization, post-hackathon run plan (offer period ends 2026-11-15) |

### Grant/bounty category mapping (for Superteam Earn)

| TARS feature | Superteam category | Track fit |
|---|---|---|
| Onchain audit trail (`TradeAuditTrail.sol`, `src/audit_trail.py`) | Developer Tools, Security | Transparency / auditing prize |
| Multi-strategy ensemble (`src/signals.py`) | AI/ML, Trading | AI trading bot |
| X Layer + native USDC | DeFi, Infrastructure | X Layer app (note: not Solana) |
| **RPC Fast Solana path (to build)** | **Infrastructure** | **Best RPC infra use — required for this sidetrack** |
| Multi-leg atomic funding-arb (`src/multi_leg.py`, `src/agent.py:684`) | DeFi, Trading | Delta-neutral / arb strategy |
| Non-overridable RiskGate (`src/execution/`) | Security, Risk | Risk-management prize |

Without the Solana path, the only honest category fit is “general project quality” — not enough for an infrastructure sidetrack.

---

## 4. Integration / implementation plan

All additions must be **opt-in via env** (empty = current OKX-only behavior) so the 280 offline tests keep passing and `AGENTS.md` checks stay green.

### 4.1 Week 1 — Settings + status endpoint (no behavior change)

`src/settings.py:96` — add:

```python
rpc_fast_url: str = ""
rpc_fast_grpc_url: str = ""
rpc_fast_api_key: str = ""
rpc_fast_plan: str = ""  # "aperture" | "stream" | "focus"
rpc_fast_timeout_seconds: PositiveInt = 30
```

`src/main.py` — add `GET /api/v1/rpc-fast-status` returning: configured?, plan, last-used cycle, latency comparison, usage totals. Returns honest `configured: false` when unset (same honesty rule as x402 paywall, `src/main.py:366-379`).

`.env.example` — document new vars + Focus vs Stream guidance + offer window.

### 4.2 Week 2 — Solana data path + metrics

New module (suggested `src/solana_rpc.py`):

- `fetch_solana_market_snapshot()` via `rpc_fast_url` (REST).
- Optional Yellowstone/Shredstream consumer behind `rpc_fast_grpc_url` (Aperture justification).
- Timeouts + error typing; never raise into trading cycle — fall back to OKX CLI.
- Metrics: `tars_rpc_fast_usage_total{source,result}`, latency, fallback count.

`src/agent.py:240` — branch: if `rpc_fast_url` set → try Solana snapshot for SOL-designated assets (or a new `SOL-USD` Solana watch asset), else OKX CLI. Tag per-asset source in cycle output so `/trade` response evidences infrastructure use.

Regression test to add: `tests/test_rpc_fast.py` — unconfigured → OKX path unchanged; RPC Fast error → fallback + counted; status endpoint honest-null.

### 4.3 Week 3 — Community presence kit

- New `src/x_poster.py`: `post_cycle_result(cycle_data)`, `post_rpc_fast_status(status)`; ≤280 chars; max 3 posts / 30 days (in-memory + log).
- `POST /api/v1/x/post-cycle/{cycle_id}`, `POST /api/v1/x/post-rpc-fast-status` (operator-only, reuse `_require_agent_token`, `src/main.py:242`).
- `docs/RPC_FAST_POST_CALENDAR.md` (or section in submission doc): 6 posts / 6 weeks, each with RPC Fast mention + metric.
- Engagement log: `data/community_log.jsonl` (date, channel, link, type: post/quote/reply).

Suggested calendar (exceeds 2–3/month):

| Week | Type | Theme |
|---|---|---|
| 1 | Cycle result | First RPC Fast-backed SOL snapshot in cycle |
| 2 | Infra | Why RPC Fast: latency numbers vs OKX CLI |
| 3 | Cycle result | Funding signal with Solana reference price |
| 4 | Educational | How write-first audit + fast RPC composes |
| 5 | Cycle result | Ensemble decision with dual-source data |
| 6 | Submission | Hackathon entry + post-hackathon run plan |

### 4.4 Week 4 — Submission package

Create `RPC_FAST_SUBMISSION.md` (repo root) covering:

1. Project (TARS one-pager + what changed for Solana).
2. Infrastructure use (endpoints used, plan choice with justification, benchmark table, `/rpc-fast-status` sample output).
3. Community presence (calendar + already-published links + TG/Discord handles).
4. Impact (projected credit utilization through 2026-11-15, post-hackathon run plan, open-source transparency value).
5. Links: Colosseum submission, Superteam sidetrack submission, application-form ID, X/TG/Discord.

Also update `README.md:70` test count and `HACKATHON_SUBMISSION.md` only if needed; do not fork submission docs unnecessarily.

---

## 5. Risks / constraints to state honestly

1. **Mainnet-only + Frankfurt:** RPC Fast per listing is mainnet, FRA-hosted. Tarstrade defaults to dry-run + X Layer testnet. Any Solana mainnet reads must be read-only unless operator explicitly opts into live.
2. **No testnet from sponsor:** cannot “try on devnet” — gate Solana path behind explicit env + dry-run-safe read-only mode.
3. **Secrets:** never commit `.env`, `AGENT_WALLET_PRIVATE_KEY`, `AGENT_API_TOKEN`, RPC Fast API keys. Gitleaks + pre-commit must stay green (`AGENTS.md` checks §2, §5).
4. **Scope guard:** do not reintroduce the two ported-bug regressions (`AGENTS.md`: `max_slippage_pct` enforcement in multi-leg dispatch; `PaperFillSimulator` must never clamp slippage).
5. **Prize is credits, not cash:** submission must show a credible 2-month infra consumption plan, not a cash-out narrative.

---

## 6. Definition of done for this sidetrack

- [ ] `RPC_FAST_URL` (+ gRPC/API key/plan) wired, documented, status endpoint live.
- [ ] At least one cycle path consumes RPC Fast (SOL watch asset or benchmark job) with metrics.
- [ ] Benchmark table (OKX CLI vs RPC Fast latency) in submission doc.
- [ ] 4–6 X posts published (2–3/month), each mentioning RPC Fast + metric; links logged.
- [ ] `@rpcfast` followed; TG + Discord joined; application form submitted (ID recorded).
- [ ] Dual submission filed (Colosseum + Superteam sidetrack); `RPC_FAST_SUBMISSION.md` committed.
- [ ] `python -m pytest tests/ -q` green; `git diff` shows no secrets.

---

*End of report.*
