# TARS — A Virtual Crypto Wall Street, Security-First

**Track:** CertiK Security Audit Credits (Colosseum Crypto World's Fair).
**Ask:** $10k audit credits against the vault-system contracts below.

## What TARS is (the 10-second version)

**A virtual crypto Wall Street.** A swarm of AI quant traders votes on
every trade. The guardrails they can't bypass: hard spending caps, a
stop-everything button, and every decision written to a public ledger
**before** the trade happens — so you can check the receipts yourself,
on-chain, without trusting us.

Think of it as a trading floor where the compliance department outranks
the traders, and every trade gets a receipt you can look up.

## Why this needs an audit

The AI traders are creative. The risk system that constrains them is
deliberately boring. But "boring" only works if it's *provably* boring —
and that's what an audit proves. The contracts below are the compliance
department: they enforce the limits, record the decisions, and hold the
deposits. If they have a bug, the guardrails are theater.

## Project description

**TARS (Trade Audit & Risk System)** solves one problem: autonomous AI
agents that trade with other people's money and no verifiable record.
The target user is the cautious depositor who wants yield with receipts —
starting with colleagues, scaling post-audit.

- Colosseum submission: [OPERATOR — link]
- Repo: [OPERATOR — public link or access instructions]

## Contracts in scope (943 lines)

| Contract | Lines | What it does (in plain English) |
|---|---|---|
| `TradeAuditTrail.sol` | 441 | **The public ledger.** Every trade decision is signed and written here BEFORE execution. If the write fails, no trade. Also enforces spending caps that can only get tighter, never looser. |
| `TradingVault.sol` | 458 | **The vault.** Holds deposits. Withdrawals are two-step (request, then collect) so there's a cooling-off window. A timed lock prevents the operator from moving money without warning. Per-transfer caps limit how much the AI can move at once. |
| `MockUSDT.sol` | 44 | Test token (not audited separately) |

Out of scope: `TreasuryMultisig.sol` (undeployed, no mainnet plan this quarter).

## Security posture today (evidence, not adjectives)

- **880-test offline suite**, all green — including 22 vault tests,
  6 signature-roundtrip tests, and EVM contract tests on a real
  blockchain emulator.
- **Foundry installed** + 18/18 multisig tests green, including a live
  reentrancy attack drill (a malicious contract tries to drain the
  vault mid-transaction — the guard stops it).
- **Slither static analysis** run on all contracts; full triage in
  `reports/slither-2026-10-09.md` (real findings fixed during the scan;
  remaining items are design choices with rationale each).
- **Key history:** one leaked testnet key (scrubbed, rotated); API keys
  with no-withdrawal permission; gitleaks pre-commit + CI.
- **Target mainnet:** X Layer (chain 1952). Testnet contracts deployed.
  Architecture is chain-agnostic — already proven on Base Sepolia and
  Solana devnet. The audit trail lives where the asset lives.

## Roadmap (6–12 months, condensed from MASTER_BUILD_PLAN.md)

1. **Now–Nov:** persistent-host hardening, carry paper-trading, this audit.
2. **Q1:** legal memo (single jurisdiction), operator first-loss tranche
   (our money absorbs losses before depositors'), bug bounty ($5–10k pool),
   phased TVL ($100 → scale on proof).
3. **Q2:** alts expansion (same venue/gate), mobile distribution
   (Android-first — deposit from a bank transfer, verify on-chain in one tap).
4. **Later:** memecoin satellite stays personal-capital-only; tokenized
   stocks gated on venue availability. Neither touches depositors.

## Team, fundraising, contact — [OPERATOR]

- Team: names, roles, X/GitHub, full-time? → [OPERATOR]
- Fundraising: bootstrapped / raising? Target round + timing → [OPERATOR]
- Regulated-market focus? → [OPERATOR]
- Scoping-call contact (Telegram/email) → [OPERATOR]
