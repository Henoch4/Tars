# Gaps the Tarstrade × Meteora DBC Integration Covers + Full Bounty Details

Companion to `meteora-dbc-integration-2026-09-21.md` (the implementation
report). This doc answers two questions: (1) precisely which gaps in Tarstrade
the DBC work covers, mapped to the bounty ideas they serve; (2) the complete
bounty listing details for reference.

---

## PART A — Gaps covered

### Gap 1 — No bonding-curve vocabulary (covers: novel curve/fee configs)

**Was missing.** A repo-wide grep for `meteora|dbc|damm|dynamic.bonding`
returned zero functional hits (only an unrelated tokenizer blob). TARS knew
perps, spot, funding rates — but nothing about bonding curves, fee schedules,
quote tokens, or graduation thresholds, i.e. the entire DBC primitive.

**Bounty idea served.** "Novel Curve or Fee Configurations for a DBC token
launch. For example. Flat Curve, Exponential Curve or Long Curve."

**What was built.** `src/execution/risk_gate.py`: `DBCCurveType`
(`flat`/`exponential`/`long`/`custom`) plus a `DBCCurveConfig` dataclass
(scale, fee_bps, graduation threshold, custom a/b/c params). The gate takes
`dbc_curve_type/fee_bps/graduation` as construction-time, validated,
fail-closed inputs — the same "boring, deterministic, non-negotiable" pattern
as every other gate parameter.

**Still open.** On-chain enforcement of the curve (posting the config to the
DBC program); graduation-threshold event detection.

### Gap 2 — No DBC-aware signal (covers: equity/stock-pair price discovery)

**Was missing.** Signals were mean-reversion, momentum, funding-rate,
funding-persistence-z, ML carry — all CEX-perp concepts. Nothing evaluated a
launch-curve regime, yet the bounty explicitly wants "price discovery for
thinly traded or newly tokenized names (xStocks, Backpack Onchain, Ondo RFQ…)".

**Bounty idea served.** "Launch Mechanics tuned for Equity / Stocks paired
launches."

**What was built.** `src/signals.py::dbc_curve_signal()`: funding-extremity
signal weighted by curve shape (exponential 1.2× — fast discovery is noisier;
long 1.1× — extended holds accumulate funding; flat 1.0×; custom 0.9× —
unproven shapes get less confidence), capped at 95%.

**Still open.** Curve-native features (bonding-curve progress %, holder
concentration, LP-lock state) as signal inputs instead of funding alone.

### Gap 3 — Multi-leg had no per-leg launch config (covers: end-to-end DBC → DAMM v2 flows)

**Was missing.** `Step` carried only venue/action/asset/ratio/slippage. A
"buy on the curve, migrate to DAMM v2" package could not express *which*
curve, *what* fee, or *when* graduation fires — and crash recovery would have
dropped such fields even if they existed.

**Bounty idea served.** "Creative end-to-end launch flows using all of our
stack (DBC, DAMM v2 and DLMM)."

**What was built.** `src/multi_leg.py`: `Step` carries
`dbc_curve/fee_bps/graduation`; `validate_steps` rejects invalid curves, fees
outside 0–10000 bps, graduations outside (0, 1]; `_save_package` /
`_load_package` and `LegResult.to_dict` / `from_dict` round-trip the fields
with `.get()` defaults so old persisted files still load; `inverse()`
preserves DBC fields on unwind legs so an abort never strands a mispriced leg.

**Still open.** The actual DBC-program execution leg inside
`LiveFillSimulator`, and a DAMM-v2 migration package type.

### Gap 4 — No launch-config presets (covers: DBC Config Preset Marketplace)

**Was missing.** Curator profiles knew conservative/standard/defensive sizing —
nothing selectable for launch mechanics. The bounty explicitly lists a preset
marketplace as a wanted idea.

**Bounty idea served.** "DBC Config Preset Marketplace — Popular launchpad
configs that builders can easily pay-to-use."

**What was built.** `config/profiles.yaml`: `dbc_flat` (50 bps fee, 0.5
graduation), `dbc_exponential` (75 bps, 0.3), `dbc_long` (100 bps, 0.7), each
with per-cohort consensus thresholds; `src/curator.py` exposes
`dbc_curve_type()` and DBC consensus settings; `src/agent.py` resolves DBC
knobs per cycle (profile default, `DBC_*` env override wins); `src/settings.py`
+ `src/main.py` + `.env.example` wire and document the defaults.

**Still open.** Publishing presets externally / pay-to-use metering (x402
wiring exists in the repo and is the natural billing rail).

### Gap 5 — No data/tooling surface for DBC pairs (covers: data streams / dev tooling)

**Was missing.** No curve-progress, fee-accrual, or graduation-proximity data
existed for terminals or builders to consume.

**Bounty idea served.** "Data Streams or Developer Tooling for trading
terminals and builders to easily plug-and-play when building a launchpad."

**What was built (foundation).** DBC parameters now flow through signals
(metadata carries `curve_type`), decisions, and persisted packages — i.e. the
data model a stream/API would serve is in place and audit-logged.

**Still open.** The `/api/v1/dbc-*` HTTP surface and any streaming endpoint.

### Coverage matrix

| Bounty idea | Gap | Status after this work |
|---|---|---|
| Novel curve/fee configs | #1 vocabulary | Config + validation done; on-chain posting open |
| Equity/stock-pair mechanics | #2 signal | Funding-weighted signal done; curve-native features open |
| End-to-end DBC→DAMM v2 flows | #3 multi-leg | Package config + persistence done; execution leg open |
| Preset marketplace | #4 profiles | Presets + selection done; external publishing/billing open |
| Data streams/tooling | #5 surface | Data model done; API/streaming open |

---

## PART B — Full bounty details (Superteam Earn listing, as posted)

- **Listing:** "Best use of Meteora's Dynamic Bonding Curve (DBC)" — by
  **Meteora** · hackathon · Submissions Open · Global.
- **Prizes — 20,000 USDC total:** 1st 10,000 · 2nd 5,000 · 3rd 3,000 ·
  4th 1,500 · 5th 500 USDC. 4 submissions at time of capture; ~22d 3h
  remaining; hackathon tracks do not require credits.
- **Track:** Crypto World's Fair track — side tracks of the latest Solana
  Global Hackathon. Skills needed: Blockchain. Winner announcement by
  **October 31, 2026** (sponsor-scheduled). Contact via listing for questions.
- **Related live tracks (same fair):** Panta API sidetrack (5,000 USDG) ·
  Superteam Nigeria track (5,000 USDG) · Superteam Vietnam track (10,000 USDG) ·
  RPC Fast infrastructure sidetrack (10,470 USDC) — all due in ~22d.
- **Premise:** "Meteora's stack is built for builders wanting to innovate on
  asset creation. Meteora's Dynamic Bonding Curve (DBC) is a fully
  configurable token launch primitive: you control the curve shape, fee
  schedule, quote token, graduation threshold, and how the pool migrates into
  Meteora DAMM v2 liquidity."
- **Goals:** "a lot left to tokenize hence we need more innovative
  launchpads… a launchpad that does stock-pairs, icm-pairs, meme-pairs,
  rwa-pairs and fully custom pair setups i.e. more asset classes on the same
  liquidity layer." Focus: RWAs, tokenized stocks, AI, memes on DBC + DAMM v2.
- **Ideas loved:** (1) equity/stock-pair launch mechanics incl. xStocks,
  Backpack Onchain, Ondo RFQ; (2) novel curve/fee configs (flat, exponential,
  long); (3) end-to-end DBC + DAMM v2 + DLMM flows (conviction pools,
  compounding-liquidity pools); (4) data streams / dev tooling; (5) DBC config
  preset marketplace.
- **Judging:** depth of Meteora integration; technical execution; originality
  and taste (new DBC use case, lasting beyond the meme-stock meta); impact
  potential (new asset classes); traction/volume (mainnet-live preferred).
  Closed-source projects: add GitHub ID `dannxbt` with read permissions.
- **Resources:** DBC dev guide (`docs.meteora.ag/developer-guides/dbc`); DAMM
  v2 dev guide; DBC program (`MeteoraAg/dynamic-bonding-curve`); DAMM v2
  program (`MeteoraAg/damm-v2`); Invent CLI + actions docs + `meteora-invent`
  repo; fun-launch scaffold guide + code; TS SDKs (`dynamic-bonding-curve-sdk`,
  `damm-v2-sdk`); agent skill + `llms-txt` context; docs MCP.
- **Dev support:** Discord (`discord.com/channels/841152225564950528/…`) and
  Telegram (`t.me/meteora_dev`).
- **Discretionary grants:** select qualified teams / winners building
  innovative AI or RWA use cases on DBC during Stocklana + Crypto World's Fair
  may be eligible for infrastructure grants.
