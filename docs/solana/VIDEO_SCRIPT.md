# VIDEO_SCRIPT.md — 2-minute Stocklana walkthrough (record: Loom/OBS, 1080p, mic on)

Target: judges asking "could this be a real app people use?" Every claim shows a tx, not a slide.

## 0:00–0:15 — Hook (terminal + Solscan tab open)
> "Tokenized stocks trade 24/7 on Solana, but every product asks you to trust the operator. Stockulus flips it: every trade decision is written on-chain BEFORE it executes. If the log fails, the trade doesn't happen. Here's the proof."

Show: README evidence table + Solscan program page.

## 0:15–0:45 — The audit trail (main track)
1. `C:\Python314\python.exe scripts\demo_live_micro.py` running (or replay output).
2. Point: signal LONG AAPLx 9000bps → risk gate $5 cap → `log_decision` tx.
3. Open decision tx on Solscan: `AvubGLJ8…` (swap) + decision `dec_0ff8e8bb4d80`.
> "Ninety-five hundred basis points of confidence, four-fifty position, logged first, filled second. Verify it yourself — the link's in the submission."

## 0:45–1:15 — The DBC pools (Meteora bounty)
1. Solscan pool page `CVrD4X…`: reserves moved 0 → 19.7M lamports quote across two swaps.
2. Explain in one line: "Token-2022 config, linear 120bps calm curve, wSOL quote because devnet has no Circle USDC — one env flip on mainnet."
3. Show `create_config.js 1.0` vs stress config code side-by-side (calm linear / stress exponential 900bps).
> "Same pool primitive, volatility-tuned curves for equity-like assets — not memecoin defaults."

## 1:15–1:45 — The agent (Clawpump bounty)
1. Clawpump dashboard: agent `Stockulus` live, skills on.
2. Honest state: "Token launch funds the last mile — STCKLS + stock-paired pool the moment ~0.15 SOL lands. Creator fees route to a public buyback wallet, tracked on the dashboard."
3. Show `clawpump_client.py` + live `list_agents` output (agent returned via API).

## 1:45–2:00 — Close
> "Devnet mirrors, real mechanics: carry math, risk gates you can't override, audit trail you can check. Code, pools, and programs are all linked below. Mainnet is a funding decision, not a technical unknown."

Show: GitHub repo + evidence table. End screen: repo URL + program IDs.

## Recording checklist
- [ ] Terminal font ≥16pt, dark theme
- [ ] Solscan tabs pre-opened (programs, pool, swap tx)
- [ ] No secrets on screen (`.env` never `cat`'d; keypair never shown)
- [ ] 2:00 hard cap — cut the Q&A, keep the txs
- [ ] Upload unlisted YouTube/Loom → paste link into README + submission form
