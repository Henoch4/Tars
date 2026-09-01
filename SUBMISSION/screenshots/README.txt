Screenshots captured (PNG) from live testnet verification, Aug 29 2026.
All against contract v0.1.3 / id 793 on T3N testnet.

LIVE OKX DATA SCREENSHOTS (final submission):

12. 12-demo-wire-format-real-okx.png — demo.ts: wire format with all 12 functions,
    agent DID, tenant DID

13. 13-demo-risk-params-mandate.png — demo.ts: risk params (full JSON), mandate,
    daily stats

14. 14-demo-trading-cycle-real-okx.png — demo.ts: REAL OKX DATA
    BTC-USDT at $77,985.6, MA5 (77885.20) ≈ MA20 (77683.98), spread 0.26%
    Crash mass: 0bps, Validation: REJECTED - No signal generated
    Pending escalations list (6 entries)

15. 15-demo-escalations-complete.png — demo.ts: escalations list, updated daily
    stats, "Demo Complete"

16. 16-verify-functions-12-exports-ml-predict.png — verify-new-functions.ts:
    all 12 enclave exports listed, ml-predict (neutral 0.485, bullish 0.638,
    bearish 0.488), compute-size ($2500, sweet_spot=true)

17. 17-verify-functions-sizing-mandate-execute.png — verify-new-functions.ts:
    compute-size, over-sweet-spot ($0), evaluate-mandate allow ($700) /
    escalate ($2000), list-escalations (7 pending), execute-plan deny
    (low confidence) / refuse (above mandate max)

OLDER SCREENSHOTS (historical, kept for reference):

1. 01-register-version-conflict.png — register.ts: version conflict bug
2. 02-demo-risk-params-mandate.png — demo.ts: risk params, mandate (old run)
3. 03-verify-functions-ml-predict.png — verify-new-functions.ts (old run)
4. 04-verify-functions-execute-plan.png — verify-new-functions.ts (old run)
5. 05-demo-wire-format.png — demo.ts: wire format (old run)
6. 06-demo-trading-cycle.png — demo.ts: OKX DNS failure (old, now fixed)
7. 07-demo-escalations-complete.png — demo.ts (old run)
8. 08-escalation-lifecycle.png — verify-escalation-lifecycle.ts (first run)
9. 09-escalation-lifecycle-clean.png — verify-escalation-lifecycle.ts (clean)
10. 10-verify-functions-12-exports-ml-predict.png — verify-new-functions.ts (clean)
11. 11-verify-functions-sizing-mandate-execute.png — verify-new-functions.ts (clean)
