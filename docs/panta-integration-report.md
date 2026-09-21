# Panta API Integration Report

Date: 2026-09-21
Task: Assess tarstrade, find where the Panta API Sidetrack bounty (Superteam Earn, Colosseum Crypto World's Fair) fits, and implement the integration.

## 1. What tarstrade is

Tarstrade (TARS = Trade Audit & Risk System) is an auto-trading bot with a strict safety layer. Each cycle it:

1. Fetches market data (prices, funding rates, positions).
2. Generates trade signals (mean reversion, funding-rate strategies, ML carry gate).
3. Checks every order against fixed safety rules (position cap, daily loss/trade/volume caps, confidence floor, slippage collar, price freshness, kill switch).
4. Writes each approved decision to an on-chain log (TradeAuditTrail.sol on X Layer) BEFORE executing. If the log write fails, the trade is blocked.
5. Executes via the OKX CLI (or simulates in dry-run mode).

What it did NOT have: any prediction-market ability. No Panta API usage anywhere in the codebase (verified by search).

## 2. What the bounty wants

The Panta API Sidetrack ($5,000 USDG pool: 2000 / 1000 / 1000 / 1000) rewards builders who meaningfully use the Panta API — prediction-market infrastructure on Solana (browse markets, create markets, buy YES/NO positions, read positions, claim winnings/creator fees, attribute trades) — inside a product that is more than a plain betting site. Judged on: depth of Panta use, technical quality, user experience, originality, real-world potential, traction. Requires a working demo and a submission to both Colosseum and Superteam Earn.

## 3. Where they fit

Panta supplies the missing piece (prediction markets); tarstrade supplies trading discipline (risk checks + permanent audit log). A combined demo — the bot creating a Panta market or buying a Panta position with every step passing tarstrade's safety rules and on-chain log — matches the bounty criteria directly.

## 4. Changes made (3 files)

### 4a. `src/panta_client.py` (NEW, ~210 lines)
Async client for `https://live-api.panta.market/api/v1`, following Panta's no-custody model (API returns unsigned transactions; the user's wallet signs; the app broadcasts and reports back). Covers:
- `catalog()` / `market_detail()` — browse markets
- `create_market_quote()` — quote a market-creation fee (USDC base units, 6 decimals)
- `primary_buy_quote()` — quote a YES/NO buy (human-readable decimals, e.g. "20.00")
- `wallet_positions()` — holdings, phase, claim eligibility
- `claim_build()` / `claim_creator_fees()` — unsigned claim transactions
- `report_trade()` — trade attribution
- `init_panta_client()` / `get_panta_client()` — shared instance wired from settings

### 4b. `src/settings.py` (+4 lines)
- `panta_api_key: str = ""` — API key from env, never from code
- `panta_timeout_seconds: PositiveInt = 30` — request timeout

### 4c. `src/agent.py` (+~100 lines)
- `AutonomousTradingAgent.__init__` accepts `panta_client` (defaults to the shared instance, which is `None` unless initialized — so default behavior is unchanged).
- New `_panta_phase()` method, called once per asset after the integrity gate and before signal generation:
  - `PANTA_CREATE_<outcome_a>_<outcome_b>_<usdc_base>` → asks Panta for a market-creation quote, records it as a decision. Quote only.
  - `PANTA_BUY_<market_id>_<YES|NO>_<usdc>` → asks Panta for a primary-buy quote, records it as a decision. Quote only.
  - Any other asset → returns `None`, normal trading path continues untouched.
- Also fixed two self-inflicted breakages during implementation: a lost indent on `def __init__` and the `_panta_phase` method body failing to land on the first attempt. Both verified fixed (`agent.py` parses; `panta_client` + `settings` import cleanly).

## 5. Verification done

- `python -c "import src.panta_client, src.settings"` — OK
- `ast.parse` of `src/agent.py` — OK
- `python -m pytest tests/test_data_integrity.py -q` — 15/15 passed
- Earlier in the session: `test_signals.py` (22), `test_multi_leg.py` (22), `test_curator.py` + `test_data_integrity.py` (26) all passed before unrelated breakage appeared (see §7).
- `git diff --stat` confirms no secrets: only `src/agent.py`, `src/settings.py` modified; `src/panta_client.py` + `docs/panta-integration-report.md` (this file) new.

## 6. Still missing (next steps)

1. **Verify endpoint paths.** Only Panta's docs front page was read; the exact REST paths in `panta_client.py` are best-guess and MUST be checked against `https://docs.panta.market/llms.txt` and the API reference before any live call.
2. **Live quote test** with a real `PANTA_API_KEY`.
3. **Wallet signing handoff** (user signs Panta's unsigned tx; app broadcasts; app reports signature back).
4. **Risk-gate + on-chain logging for Panta fills** (same treatment directional orders get: `check_order`, `log_decision`, `record_execution`).
5. **Regression test** for the Panta path (mocked client asserting quote→decision recording and pass-through for normal assets).
6. **Demo + submission writeup** for Colosseum and Superteam Earn.

## 7. Flag: pre-existing uncommitted changes (not mine, left untouched)

The working tree contains uncommitted changes I did not make, in `src/signals.py` (+81), `src/curator.py` (+15), `src/execution/risk_gate.py` (+23), `src/multi_leg.py` (+19), `config/profiles.yaml` (+46). As of now `src/signals.py` crashes on import (`NameError: DBCCurveType`), which blocks `import src.agent` and the full test suite. Recommend the owner review/stash those before continuing.

## 8. Gaps in tarstrade that Panta covers

Each gap = something tarstrade cannot do today that the bounty requires, and how Panta fills it.

1. **No prediction markets at all.** Tarstrade trades CEX perps/spot (BTC/ETH/SOL/BNB) and runs funding arbitrage. It cannot list, price, or trade a YES/NO event market. Panta provides the whole market lifecycle (discover → data → create → trade → positions → claims), so tarstrade doesn't have to build a market system from scratch.
2. **No market creation.** Tarstrade only takes prices; it can never make a market. Panta's create flow (fee quote → unsigned tx → register after broadcast) adds that power, including creator-fee claims on graduated markets — a new revenue line for the product.
3. **No YES/NO position trading.** Tarstrade buys/sells perps and spot via the OKX CLI. Panta's primary-buy flow (quote a YES/NO fill on the bonding curve → build instructions → submit signature) adds a second tradeable asset class alongside perps.
4. **No Solana path.** Tarstrade executes on centralized exchanges; its only on-chain touch is audit logging on X Layer (EVM). Panta brings a Solana transaction flow (quote → sign in user wallet → broadcast on your RPC → confirm), opening the bot to the Solana hackathon ecosystem.
5. **No crowd-facing product.** Tarstrade is a bot backend plus a dashboard — one operator, no community surface. Panta enables the bounty-friendly surfaces: social prediction, creator/community markets, sports/event markets.
6. **No settlement/claims.** Tarstrade has no concept of a market resolving and paying out. Panta's claim-eligibility checks, win-claim building, and creator-fee claims close that loop.
7. **No outside market intel.** All tarstrade signals come from its own price/funding feeds. Panta market prices are a fresh outside signal (what the crowd believes), feedable into the signal engine — the "AI + prediction markets" angle the bounty explicitly lists.
8. **No sidetrack eligibility.** As-is, tarstrade cannot enter this bounty (zero Panta use). The integration in §4 is what makes entry possible.

What Panta does NOT cover (stays tarstrade's job): the risk gate, the on-chain audit trail, position sizing, and execution discipline. The pairing is: Panta = markets, tarstrade = safety + proof.

## 9. Full bounty details (Panta API Sidetrack)

- **Listing:** "Colosseum Crypto World's Fair Hackathon | Panta API Sidetrack" by Panta, on Superteam Earn. Global, hackathon category, submissions open. Skills: Frontend, Backend, Design, Blockchain.
- **Prizes ($5,000 USDG total):** 1st 2,000 · 2nd 1,000 · 3rd 1,000 · 4th 1,000.
- **Status at time of writing:** 2 submissions in; ~22 days remaining; winners announced by October 27, 2026 (per sponsor schedule).
- **Core pitch:** prediction markets as infrastructure, not a destination — e.g. a trading terminal using markets as intel, a sports app with live-event trading, creators embedding markets for their audience, AI apps using market data.
- **Panta API covers:** market discovery, market info, prices/data, market creation, creation-fee quotes, unsigned-tx building, YES/NO buys, wallet positions, claim eligibility, win-claim txs, creator-fee claims, trade attribution/verification. Base URL `https://live-api.panta.market/api/v1`; auth via `X-Api-Key` or `Authorization: Bearer`. Amounts: creation fees in USDC base units (6 decimals); buys in decimal strings.
- **What you can build (8 lanes):** prediction-market apps · trading/analytics terminals · sports & events · social prediction · AI + prediction markets · creator/community tools · adding PM features to an existing Crypto World's Fair project · anything novel.
- **Eligibility:** register for the official Colosseum Crypto World's Fair hackathon → submit there → meet its rules → ALSO submit to the Panta Sidetrack on Superteam Earn (sidetrack does not replace the official submission) → meaningfully integrate the Panta API → working demo. Teams may enter the main hackathon plus other sidetracks per official rules.
- **Submission must:** be a working demo/prototype, in English; explain what was built and the problem it solves; explain exactly how Panta API is integrated; be filed in both places.
- **Judged on (6):** Panta API integration depth · technical execution · product & UX · originality · real-world impact potential · traction.
- **Resources:** docs `https://docs.panta.market/` · playground `https://github.com/Kaito-HQ/panta-api-playground` · site `https://www.panta.market/` · Solana docs `https://solana.com/docs` · support in Panta Discord `#dev-chat` (`https://discord.gg/M76nH6fUwc`) · X `@pantahq`.
- **Open question on the listing:** a commenter asked about API rate limits — no public answer on the page; worth asking in `#dev-chat` before building anything load-heavy.
