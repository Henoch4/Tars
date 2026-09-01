# BUGS.md — T3N submission findings (TARS trading risk gate)

Verified against **testnet** cluster, `@terminal3/t3n-sdk`, Node on Windows,
Rust 1.97.1, target `wasm32-wasip2`, `wit-bindgen 0.49`, Aug 2026.

---

### 1. `wasm32-unknown-unknown` is not installed by default — docs assume it

`cargo check --target wasm32-unknown-unknown` fails with
`error[E0463]: can't find crate for 'core'` on a fresh rustup install; only
`wasm32-wasip2` is present. The quickstart docs never state which WASM target
is required/preinstalled, and the error message ("may not be installed")
suggests the wrong target for component-model contracts.

**Impact**: first-time builders lose time chasing the wrong target.
**Suggested fix**: docs should pin `wasm32-wasip2` explicitly, and/or the
quickstart should `rustup target add wasm32-wasip2`.

---

### 2. Tenant-contract WIT worlds ship no host clock — every contract reinvents time

`host:tenant/tenant-context@1.0.0` exposes `cluster-timestamp-secs()` (good!),
but nothing in the docs or reference contracts demonstrates using it for
*date* semantics. Without it, a naive implementation hardcodes "today" — our
first draft did exactly that (`"2026-08-27"`) — which silently breaks any
daily-limit contract: counters never roll over and KV keys collide across
days. There is no `now()` convenience and no date formatting in any host
interface, so every tenant contract must reimplement civil-date conversion
from epoch seconds (no_std + alloc: no `chrono`).

**Impact**: silent correctness bug class for any time-windowed policy
(daily quotas, rate windows, 24h resets).
**Suggested fix**: ship a `host:interfaces/clock`-style helper or a documented
snippet showing `cluster-timestamp-secs()` -> `YYYY-MM-DD` conversion.

---

### 3. Re-registration re-mints contract_id and orphans map ACLs (carried from ADK submission, still unresolved)

`tenant.contracts.register` rejects same-version re-upload ("version 0.1.1 is
not higher than current version 0.1.1") and every new registration mints a
new `contract_id`, invalidating the `maps.update` reader/writer grants made
for the old id. Any hotfix cycle = version bump + map re-grant + orphaned ACL.

**Impact**: no in-place upgrade path; CI-style register flows break silently.
**Suggested fix**: `maps.update(tail, ...)` should resolve to the *current*
contract id automatically, or accept `only: [contract-owner]` semantics.

---

### 4. `fuel_per_minute` burning on KV-heavy iteration (carried from ADK submission, still unresolved)

~10 KV-heavy invocations + a re-register within one minute can trip
`RPC Error: quota exceeded (fuel_per_minute)`. No per-call fuel estimate is
surfaced by the SDK, so the first signal a developer gets is a hard RPC error.

**Suggested fix**: surface estimated fuel per invocation in the SDK response
metadata, or return `Retry-After`-style metadata on quota errors.

---

### 5. `token.balance` / `token.get-usage` still fail on fresh wallets (carried from ADK submission)

`getBalance()` → `DOMException [InvalidCharacterError]` in `atob`;
`getUsage()` → `invalid token.get-usage params`. Blocks programmatic proof of
the claimed free tokens.

---

### 6. `kv_store.scan` can miss entries written moments earlier (NEW this challenge)

We built an escalation queue on the tenant `escalations` map: `create`
writes a row (via `kv_store.get`/`put` under a composite `z:<tid>:escalations`
key), then a separate `list-escalations` invocation scans the same map with
`kv_store.scan(map, [], [], limit)`. The scan returned `[]` even though a
follow-up point-read by key (`kv_store.get`) found the row and `resolve` by id
succeeded. In other words: **writes were durable and point-`get` consistent,
but the range `scan` did not surface the freshly-written key** in the same
visibility window. This is reproducible on testnet, not a parse bug — the same
contract's `decisions` lifecycle (which never relies on `scan`) shows no
equivalent issue.

**Impact**: any enumerate-a-then-b pattern (queues, recent-logs, dashboards)
that calls `scan` in a *later* transaction can silently under-report.
**Workaround used**: maintain a fixed-key pending-index array in the same map
that `create`/`resolve` update, and have `list` read that index with point
`get`s instead of `scan` (see `mandate.rs` `_pending_index`). Verified working
across separate invocations.
**Suggested fix**: document/harden the scan visibility semantics — ideally
per-transaction linearizability for keys written by the same tenant, or a
cursor/paging primitive (the WIT already notes scan is one-shot with no
cursor).

---

## SDK features confirmed GOOD

- `T3nClient.authenticate` (Eth sign), `setEnvironment("testnet")`.
- `tenant.contracts.register` + `maps.create/update/entrySet` (create is
  idempotent / update re-grants ACLs — used for every re-register).
- `T3nClient.executeAndDecode` for all 12 contract exports, including the
  owner-gated admin functions.
- `host:tenant/tenant-context@1.0.0` `cluster-timestamp-secs()` + `seq-no()`
  — used by our contract for daily rollover and collision-free decision / 
  escalation IDs (`dec_<secs>_<seq>_<asset>`, `esc_<secs>_<seq>`).
- `logging.info` from inside the enclave readable via `tenant.contracts.logs`.
- `kv-store@2.1.0` put/get with `z:<tid>:` namespacing — all five maps
  (secrets, decisions, daily-stats, mandates, escalations) work as documented
  for point reads/writes (scan caveat is finding #6).
