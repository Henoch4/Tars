# GTA VI Event Overlay — Build Slice 1

Status: policy and test scaffolding only. No wallet, service, or live broadcast
is enabled.

The existing `tars-satellite` remains the general memecoin data collector. The
GTA overlay is a separate policy boundary for Solana candidates associated with
the GTA VI narrative. It does not buy based on a name match: a candidate must
pass every hard check and the event window must be active.

## Current policy

- disabled by default (`enabled: false`);
- prepared budget: $9 total, $3 per token, three open positions;
- explicit UTC event window;
- separate total and per-token USD ceilings;
- maximum open-position count;
- keyword allow-list used only for candidate generation;
- mint and freeze authorities must be confirmed disabled;
- honeypot/sellability must be explicitly cleared;
- transfer tax must be zero when supplied;
- top-10 ownership must be at or below 20%;
- holder count must be at least 100;
- liquidity must be at least 10x intended position size;
- bundled/sniper concentration above 25% is rejected when supplied;
- missing hard-filter data fails closed.

Prepared exit policy (not yet wired to a live runner): 35% hard stop, sell 45%
at +100%, sell 30% at +200%, trail the remainder at 25%, cap holds at 96 hours,
and force flat at the end of the event window.

The implementation is pure policy code in `src/gta_event.py`. It has no network
calls, private-key access, wallet interaction, or exchange/broadcast path.
`config/gta_event.yaml` is a disabled configuration template and must not be
treated as a live authorization.

## Next build slices

1. Read-only Solana candidate collector and rejection report.
2. Shadow-mode replay against the existing satellite data without changing its
   filters or state.
3. Separate GTA state, breaker, and audit paths.
4. Operator-supplied hard budgets and a declared event window.
5. Only after a clean smallest-size round trip: separately reviewed live wiring.

The orchestration shell is now implemented in `src/gta_live_runner.py` with
explicit `DISABLED`, `RECONCILING`, `ARMED`, `RUNNING`, and
`EMERGENCY_SHUTDOWN` states. Discovery and exit I/O are injected, so the state
machine can be tested without network or wallet access. It auto-arms only when
both policy flags, budgets, and the UTC event window permit it.
