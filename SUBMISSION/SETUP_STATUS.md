# SETUP_STATUS.md — what's done / what's left before submitting

## Done

- [x] Full TEE contract built: 12 exports (validate-trade, log-decision,
      get-risk-params, get-daily-stats, set/get/evaluate-mandate,
      resolve/list-escalations, compute-size, ml-predict, execute-plan).
- [x] 12 offline Rust unit tests pass (mandate, sizing, ML).
- [x] Release WASM built: `t3n/contract/target/wasm32-wasip2/release/z_trading_risk_gate.wasm`
      (308,953 bytes, clean build).
- [x] Contract **registered on testnet, contract_id 793, v0.1.3** (via
      `npx tsx src/register.ts`); all 5 maps ACL'd, risk params + mandate
      seeded.
- [x] All 12 exports verified LIVE on testnet via `executeAndDecode`
      (`verify-new-functions.ts`, `verify-escalation-lifecycle.ts`) — recorded
      in `SUBMISSION/verification/LIVE_OUTPUTS.md`.
- [x] `kv_store::scan` visibility bug found + worked around (get-based pending
      index in `mandate.rs`); added as BUGS.md #6.
- [x] `SUBMISSION/README.md` updated for v0.1.3 (full capability table,
      architecture, risk policy, mandate, handover).
- [x] `GOOGLE_DOC_OUTLINE.md` updated for v0.1.3.
- [x] `verification/README.md` updated (12 exports, real sizes/facts).
- [x] `twitter_thread.md` rewritten for the T3N bounty, tags `@terminal3io`.

## Left (in order)

1. **WIT extract (optional corroboration)**: `wasm-tools component wit
   <wasm> > SUBMISSION/verification/z_trading_risk_gate.wit.txt`. `cargo
   install wasm-tools` failed to complete earlier (network flaky); retry when
   net is stable. NOTE: not blocking — live testnet registration + all-12-exports
   verification already proves the component and exports.
2. **Screenshots**: capture the 6 shots in `screenshots/README.txt` from the
   live outputs (registration, KV seed, risk-params/mandate reads, ml-predict,
   compute-size, evaluate-mandate allow/escalate, execute-plan, escalation
   lifecycle, service `/trade`).
3. **Public repo + Google Doc**: push repo public (verify no `.env` committed
   — `t3n/.gitignore` and root `.gitignore` cover them; grep `git diff` before
   pushing), paste `GOOGLE_DOC_OUTLINE.md` into a public Google Doc.
4. **Bonus**: post the X thread (`twitter_thread.md`, now T3N-specific) tagging
   `@terminal3io` — swap in the public repo URL.
5. **Submit** on Superteam Earn (1 credit) — sooner scores better.
   Deadline Sept 16, 2026; winners Sept 23, 2026.
