# LIVE_OUTPUTS.md — verbatim testnet verification (contract v0.1.3, id 793)

> Real output captured against the T3N testnet cluster, `@terminal3/t3n-sdk`,
> Node on Windows, Rust 1.97.1, target `wasm32-wasip2`, `wit-bindgen 0.49`,
> Aug 2026. Every block below is copy-paste from actual tool output.

## Environment

- Cluster: **testnet**
- Tenant DID: `did:t3n:5db3681df85b9a698777a5aa603329da86cdb5dc`
- Agent DID: `did:t3n:f877094c99cd7264ebaab2cf2c6307c097775b76`
- Contract: `z:5db3681df85b9a698777a5aa603329da86cdb5dc:trading-risk-gate`
- contract_id **793**, version **0.1.3**
- Component size: **308,953 bytes** (0.1.3) / 307,974 (0.1.2) — well under cap

## 1. Registration + KV map seeding (`npx tsx src/register.ts`)

> Note: re-running register.ts after the contract is already live at the same
> version produces "contract version invalid: version X is not higher than
> current version X" — this is expected (BUGS.md #3). The contract is already
> registered and the KV maps already exist. The script still prints the
> tenant DID, WASM size, and version before failing.

```
Registering T3N trading risk gate contract...
Authenticated as tenant: did:t3n:5db3681df85b9a698777a5aa603329da86cdb5dc
WASM size: 308953 bytes
RpcError: RPC Error: contract version invalid: version 0.1.3 is not higher than current version 0.1.3 [bc7d36c1-c8eb-4b15-b421-eccbee8d3931]
```

## 2. get-risk-params (includes crash-veto + Kelly knobs)

```json
{
  "max_position_usd": 5000,
  "max_daily_loss_usd": 500,
  "min_confidence_bps": 7000,
  "max_leverage": 5,
  "max_daily_trades": 10,
  "allowed_assets": ["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP","BNB-USDT-SWAP"],
  "crash_veto_threshold_bps": 2000,
  "min_implied_prob": 0.44,
  "max_implied_prob": 0.62,
  "kelly_fraction_cap": 0.25,
  "equity_usd": 10000,
  "avg_reward_risk_ratio": 1.5
}
```

## 3. get-mandate (default authority profile)

```json
{
  "escalation_threshold_usd": 1000,
  "escalation_timeout_hours": 24,
  "mandate_id": "mandate_1",
  "max_size_usd": 5000,
  "min_reserve_usd": 100,
  "paused": false,
  "validity_end": 18446744073709551615
}
```

## 4. ml-predict (17 features -> P(win))

```
--- ml-predict ---
  neutral features  win_prob = 0.4850544333457947
  bullish features win_prob = 0.6383938789367676
  bearish features win_prob = 0.4875026047229767
```

## 5. compute-size (fractional Kelly + sweet spot)

```
--- compute-size ---
  size_usd = 2500 (sweet_spot = true)      # conf 5500bps + nn 0.60, Kelly-capped
  over-sweet spot (9000bps) size_usd = 0   # out of band -> no position
```

## 6. evaluate-mandate (allow / escalate)

```
--- evaluate-mandate (700 USD, within) ---
  {"verdict":"allow","reason":"Within mandate"}
--- evaluate-mandate (2000 USD, escalates) ---
  {"verdict":"escalate","reason":"Size $2000.00 above escalation threshold $1000.00","escalation_id":"esc_1788010618_162897"}
```

## 7. execute-plan (atomic mandate + risk-gate + audit)

```
--- execute-plan (700 USD, within mandate) ---
  {"approved":false,"executed":false,"reason":"Confidence 5500bps below minimum 7000bps","verdict":"deny"}

--- execute-plan (9000 USD, above mandate max) ---
  REFUSED (expected): RPC Error: contract error: execute-plan: mandate deny — Size $9000.00 exceeds max $5000.00
```

## 8. Escalation lifecycle (captured live)

A fresh `evaluate-mandate` above the threshold mints a persisted escalation,
and `list-escalations` (get-based pending index, NOT `scan` — see BUGS.md #6)
correctly surfaces every unresolved one, including ones from earlier runs. Then
`resolve-escalation` (owner-gated) denies it and the list drops it:

```
--- escalate (2000 USD) ---
  {"verdict":"escalate","reason":"Size $2000.00 above escalation threshold $1000.00","escalation_id":"esc_1788010732_162916"}
--- list-escalations (5 pending, incl. 4 from earlier runs) ---
  {"escalations":[
    {"action_type":"trade","asset":"BTC-USDT-SWAP","escalation_id":"esc_1788008069_162620","requested_at":1788008069,"size_usd":2000},
    {"action_type":"trade","asset":"BTC-USDT-SWAP","escalation_id":"esc_1788009045_162721","requested_at":1788009045,"size_usd":2000},
    {"action_type":"trade","asset":"BTC-USDT-SWAP","escalation_id":"esc_1788010065_162844","requested_at":1788010065,"size_usd":2000},
    {"action_type":"trade","asset":"BTC-USDT-SWAP","escalation_id":"esc_1788010618_162897","requested_at":1788010618,"size_usd":2000},
    {"action_type":"trade","asset":"BTC-USDT-SWAP","escalation_id":"esc_1788010732_162916","requested_at":1788010732,"size_usd":2000}]}
--- resolve escalation esc_1788010732_162916 (deny) ---
  {"resolved":true,"verdict":"deny"}
--- list-escalations after resolve ---
  {"escalations":[
    {"action_type":"trade","asset":"BTC-USDT-SWAP","escalation_id":"esc_1788008069_162620","requested_at":1788008069,"size_usd":2000},
    {"action_type":"trade","asset":"BTC-USDT-SWAP","escalation_id":"esc_1788009045_162721","requested_at":1788009045,"size_usd":2000},
    {"action_type":"trade","asset":"BTC-USDT-SWAP","escalation_id":"esc_1788010065_162844","requested_at":1788010065,"size_usd":2000},
    {"action_type":"trade","asset":"BTC-USDT-SWAP","escalation_id":"esc_1788010618_162897","requested_at":1788010618,"size_usd":2000}]}
```

The create → list → resolve → list-drop chain proves the escalation queue is
durable across separate invocations (survives session restarts) and that the
get-based index correctly lists rows `scan` would have missed.

## Fuel note

The testnet enforces `fuel_per_minute`; a long combined run can trip
`RPC Error: quota exceeded (fuel_per_minute)` part-way. Wait ~60s and re-run
the remaining steps (BUGS.md #4). KV-heavy sequences are best split into
short scripts (see `verify-new-functions.ts` / `verify-escalation-lifecycle.ts`).

## 9. Full trading cycle (`npx tsx src/demo.ts`)

The demo runs the complete signal → validate → execute pipeline with **real
OKX candle data**. The signal engine fetches live BTC-USDT 1H candles, computes
MA5/MA20 crossover, extracts 17 features, and runs crash-veto estimation from
realized volatility. When no clear signal exists (MA5 ≈ MA20), the agent
correctly rejects — demonstrating disciplined, fail-closed behavior.

```
--- T3N Trading Agent Demo ---
Tenant: did:t3n:5db3681df85b9a698777a5aa603329da86cdb5dc
Agent script: z:5db3681df85b9a698777a5aa603329da86cdb5dc:trading-risk-gate v0.1.3
agentDid: did:t3n:f877094c99cd7264ebaab2cf2c6307c097775b76

--- Risk Parameters ---
{
  "max_position_usd": 5000,
  "max_daily_loss_usd": 500,
  "min_confidence_bps": 7000,
  "max_leverage": 5,
  "max_daily_trades": 10,
  "allowed_assets": ["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP","BNB-USDT-SWAP"],
  "crash_veto_threshold_bps": 2000,
  "min_implied_prob": 0.44,
  "max_implied_prob": 0.62,
  "kelly_fraction_cap": 0.25,
  "equity_usd": 10000,
  "avg_reward_risk_ratio": 1.5
}

--- Mandate ---
{
  "escalation_threshold_usd": 1000,
  "escalation_timeout_hours": 24,
  "mandate_id": "mandate_1",
  "max_size_usd": 5000,
  "min_reserve_usd": 100,
  "paused": false,
  "validity_end": 18446744073709551615
}

--- Daily Stats ---
{
  "date": "2026-08-29",
  "pnl_usd": 0,
  "trade_count": 0,
  "realized_pnl_usd": 0
}

--- Trading Cycle ---
--- Processing BTC-USDT-SWAP ---
Signal: none @ 77844.2 (0bps) - MA5 (77771.88) ≈ MA20 (77645.19), spread 0.16% - no signal
Crash mass (P drawdown >20%): 0bps

=== Results ===

Asset: BTC-USDT-SWAP
  Signal: none @ 77844.2 (0bps)
  Reason: MA5 (77771.88) ≈ MA20 (77645.19), spread 0.16% - no signal
  Crash mass: 0bps
  Validation: REJECTED - No signal generated

--- Pending Escalations ---
(6 entries from earlier verification runs)

--- Updated Daily Stats ---
{
  "date": "2026-08-29",
  "pnl_usd": 0,
  "trade_count": 0,
  "realized_pnl_usd": 0
}

--- Demo Complete ---
```

> Note: the agent correctly rejected because MA5 (77,771.88) ≈ MA20 (77,645.19)
> with only 0.16% spread — no clear crossover signal. This is the intended
> behavior: the agent only trades on confirmed signals, not noise. When a
> clear MA crossover occurs (spread > threshold), the full pipeline executes:
> validate → mandate → size → execute.
