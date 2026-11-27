# Restart #5 Deployment Runbook — Solana Memecoin Satellite Entry-Context Capture

**Target:** Friday, October 30, 2026 — **09:09 UTC / 10:09 WAT** (day-7 gate)  
**VPS:** `root@80.190.82.127`, service `tars-satellite`  
**Data dir:** `/opt/tars/data/meme_satellite/`  
**Deploy method:** md5-verified scp + backup + restart (never `git pull` on the VPS)

---

## 1. Deployment gate

**Do not deploy before 09:09 UTC / 10:09 WAT on October 30.** Reaching the deadline does not automatically authorize deployment. Before proceeding:

1. Confirm the UTC window has ended.
2. Record the G1 outcome from `scripts/meme_g1_report.py --attribution` and append the verdict to `LIVE_TRADING_PLAYBOOK.md` §12.
3. Confirm G1 status (pass/fail) and whether deployment is authorized.
4. Confirm the current process health, last known working commit, and VPS backup destination.
5. Confirm the release artifact matches the offline-tested commit.

If the gate is not approved or any precondition fails, **stop at this stage.** Do not restart the running process merely to meet the date.

---

## 2. Scope of restart #5 — one change, instrumentation only

Restart #5 is an **additive data-capture release**. It does not change any entry/exit/filter/sizing/sizing rule. Window #4 remains valid; restart #5 opens a fresh G1 clock under one stable rule set.

### In scope

| Field | Where persisted | Source branch |
|-------|----------------|---------------|
| `entry.price_change` {m5,h1,h6,h24} | entry blotter rows | `feat/entry-context` (`f909d30`) |
| `entry.txns_windows` {m5,h1,h6,h24} | entry blotter rows | `feat/entry-context` (`f909d30`) |
| `entry.fdv_usd` | entry blotter rows | `feat/entry-context` (`f909d30`) |
| `entry.age_s` | entry blotter rows | `feat/entry-context` (`f909d30`) |
| `entry.mcap_usd` | entry blotter rows | `feat/entry-context` (`f909d30`) |
| `screen.price_change` | screen blotter rows | `feat/entry-context` (`f909d30`) |
| Confirm stage code (unarmed) | `src/meme_confirm_stage.py` | `wip/restart-5-confirmation-stage` (`b6deb47`) |

**Confirm stage ships unarmed (`armed=False`)**: it measures LP drain velocity but does not reject any entry. It becomes a veto only at a later declared restart.

### Out of scope — do not include in this release

- `entry.bundle_cluster_pct` / `entry.bundle_complete` → deferred to restart #6 (`TARS_BUNDLE_SCAN=1`)
- `TARS_BREAKERS=1` → durable breakers built on `feat/durable-breakers` (`3a627f0`) but undeployed; ships at its own declared restart
- Any change to `MAX_*`, `EXIT_*`, `RUG_*`, or other `src/meme_satellite.py` constants
- Any change to cooldowns, session caps, adaptive sizing, or clamp logic
- Any change to the VPS before the gate closes
- Any push to the public repo without explicit approval

### Research questions this release enables

| Question | Answerable after #5? | Notes |
|----------|---------------------|-------|
| Q1: Do rug outcomes cluster by `dev_mints` / creator launch count? | **Partial** — `dev_mints` is collected via Jupiter profile but **not currently persisted** to entry blotter rows. Verify branch before claiming Q2/Q1 answerable. |
| Q2: Do rugs show higher top-10 / creator concentration than pivots? | **No** — `top10_hold_pct` and `creator_hold_pct` are **not** persisted to entry rows in the current branch. This is a gap to flag, not assume closed. |
| Q3: Does h6 run-position at entry predict rug outcome? | **Yes** — `price_change.h6` is persisted on entry rows. |
| Q5: Does bundle cluster share differ between rugs and pivots? | **No** — deferred to restart #6. |

> **Verification required before #5:** Inspect `src/meme_loop.py` `try_enter` and `screen_candidates` to confirm exactly which fields are written to entry/screen rows. Do not claim Q1/Q2 are answerable until you verify `top10_hold_pct`, `creator_hold_pct`, and `dev_mints` persistence. If they are missing, record that as a known gap and treat Q1/Q2 as restart-#6-or-later questions.

---

## 3. Branches and base state

Both branches are **local-only, unpushed, stacked on `main`**:

```
main                          (trunk, window #4 rules)
├── feat/entry-context        f909d30  (capture only, verdict-invariance pinned)
└── wip/restart-5-confirmation-stage  b6deb47  (confirm stage, default OFF)
```

### Pre-deploy checks (offline, local machine)

```bash
# 1. Clean tree on main
git status --short          # must be empty
git log --oneline -1        # record base commit

# 2. Rebase confirm-stage onto main (if not already done)
git checkout wip/restart-5-confirmation-stage
git rebase main

# 3. Rebase entry-context onto main (if not already done)
git checkout feat/entry-context
git rebase main

# 4. Assemble deploy branch locally (do not push)
git checkout -b deploy/restart-5-confirmation-stage
git merge --no-ff feat/entry-context
git merge --no-ff wip/restart-5-confirmation-stage
git commit -m "deploy: restart #5 entry-context capture + confirm stage (instrumentation only)"

# 5. Record md5 of every file that will ship
md5sum \
  src/meme_discovery.py \
  src/meme_satellite.py \
  src/meme_feed.py \
  scripts/meme_runner.py \
  src/meme_loop.py \
  scripts/meme_entry_context.py \
  src/meme_confirm_stage.py \
  tests/test_entry_context.py \
  tests/test_meme_confirm_stage.py \
  > deploy/restart-5-md5.txt
```

Save `deploy/restart-5-md5.txt` — it travels to the VPS and is re-checked after scp.

### Offline testing required before deploy

1. **Branch inspection**: Review the full diff of the deploy branch. Confirm only data-capture code changed — no decision constants, no threshold edits, no sizing changes.
2. **Data persistence test**: Use controlled sample entries to confirm each in-scope field is recorded with expected type, units, and observation time.
3. **Missing-data test**: Simulate unavailable API fields, missing holder records, incomplete transaction windows. Confirm unknown values persist as `null`/missing-data status, never silently as zero.
4. **Decision-path parity**: Replay identical historical inputs through old and updated code. Confirm capture does not alter eligibility, entry/exit signals, position sizes, or simulated execution.
5. **Backwards compatibility**: Run `scripts/meme_blotter_mine.py` against an older blotter copy. Confirm older records without new fields remain readable.
6. **Rollback package**: Identify every file the release replaces, prepare clean deployment artifact, calculate checksum, document process manager and restart command.

> Compilation passing is not sufficient. The key test is that new information is recorded without changing the existing decision path.

---

## 4. Pre-deploy backup on VPS

SSH into the VPS **before** copying anything:

```bash
ssh root@80.190.82.127

TS=$(date +%Y%m%d%H%M%S)
BACKUP_DIR="/opt/tars/backup/restart-5-pre-${TS}"
mkdir -p "${BACKUP_DIR}"

# Copy (not move) live state — originals stay in place
cp -a /opt/tars/data/meme_satellite/blotter.jsonl      "${BACKUP_DIR}/"
cp -a /opt/tars/data/meme_satellite/positions.json     "${BACKUP_DIR}/"
cp -a /opt/tars/data/meme_satellite/G1_CLOCK_RESTART.json "${BACKUP_DIR}/"
cp -a /opt/tars/data/meme_satellite/deployer_history.json "${BACKUP_DIR}/"
cp -a /opt/tars/data/meme_satellite/HALT               "${BACKUP_DIR}/" 2>/dev/null || true

# Record running service state
systemctl status tars-satellite --no-pager > "${BACKUP_DIR}/service-status.pre.txt"

# Verify backups
md5sum "${BACKUP_DIR}"/* > "${BACKUP_DIR}/md5.pre.txt"
ls -la "${BACKUP_DIR}"
```

Record `${BACKUP_DIR}` — it is the rollback anchor.

---

## 5. Transfer and verify artifact

From the local machine, copy each file individually and verify md5 after each copy:

```bash
VPS="root@80.190.82.127"
REMOTE_DIR="/opt/tars/src"   # verify this path matches the VPS layout

for f in \
  src/meme_discovery.py \
  src/meme_satellite.py \
  src/meme_feed.py \
  scripts/meme_runner.py \
  src/meme_loop.py \
  scripts/meme_entry_context.py \
  src/meme_confirm_stage.py \
  tests/test_entry_context.py \
  tests/test_meme_confirm_stage.py; do

  scp "${f}" "${VPS}:${REMOTE_DIR}/${f}"
  echo "=== ${f} ==="
  ssh "${VPS}" "md5sum ${REMOTE_DIR}/${f}"
done

scp deploy/restart-5-md5.txt "${VPS}:/opt/tars/deploy/restart-5-md5.txt"
```

**After scp, on the VPS:** re-run md5 against the local record:

```bash
ssh root@80.190.82.127
cd /opt/tars
md5sum -c deploy/restart-5-md5.txt   # all must print "OK"
```

If any hash differs, **abort** — do not restart.

---

## 6. Restart sequence (single change)

On the VPS:

```bash
systemctl stop tars-satellite
systemctl start tars-satellite
systemctl status tars-satellite --no-pager
journalctl -u tars-satellite -n 30 --no-pager
```

**Verify the first tick:**
- Service starts without traceback
- First cycle logs show screened/passed/entered/exited counts
- No new rejection reasons appear (no logic change)
- `G1_CLOCK_RESTART.json` still present and readable

**Do not** toggle `TARS_BREAKERS`, `TARS_BUNDLE_SCAN`, or any env var in this restart. Those are separate declared changes for later restarts.

---

## 7. Post-restart verification checklist

| # | Check | Method | Pass criterion |
|---|-------|--------|----------------|
| 1 | Release commit recorded | `git log --oneline -1` on deploy branch | Matches approved commit |
| 2 | Live files backed up | `ls -la /opt/tars/backup/restart-5-pre-*` | Backup dir exists with blotter/positions/clock |
| 3 | Transfer checksum verified | `md5sum -c deploy/restart-5-md5.txt` on VPS | All "OK" |
| 4 | Process restarted & healthy | `systemctl status tars-satellite` | active (running) |
| 5 | Decision behavior unchanged | Screen `rejected_by` mix vs pre-deploy | Same distribution (unconfirmed dominant, no new codes) |
| 6 | New fields persist | `tail -3 /opt/tars/data/meme_satellite/blotter.jsonl` | Entry rows carry `price_change`, `txns_windows`, `fdv_usd` |
| 7 | Missing data explicit | Check a sample entry with absent API fields | Fields are `null`, not `0` |
| 8 | Old records readable | `python scripts/meme_blotter_mine.py <old_blotter>` | Exits 0, no crash on missing fields |
| 9 | Paper-only mode unchanged | Confirm `paper: true` on entry records | All entries `paper: true` |
| 10 | Bundle scan deferred | Confirm `TARS_BUNDLE_SCAN` not set to `1` | Env unchanged |

Mark items complete only after observing the result. The checklist is a tracking aid, not proof of success.

---

## 8. Rollback criteria

Roll back to the previously working release if any of the following occur:

- Process fails to start or enters a restart loop
- New data fields cause exceptions or disrupt the blotter
- Replay/deployment checks reveal a change to the decision path
- Running process behaves differently from approved paper-only configuration

**Rollback procedure:**

1. `systemctl stop tars-satellite`
2. Restore backed-up versions of every replaced file from `${BACKUP_DIR}`
3. Restore previous release configuration without new flags or strategy changes
4. `systemctl start tars-satellite` (once)
5. Confirm process health; record rollback commit, checksums, timestamp, and reason

Do not delete new logs during rollback — they may be needed for diagnosis.

---

## 9. What this release will and will not tell you

After restart #5, label the resulting dataset by collection period and report sample size, missingness, and outcome definitions before drawing conclusions.

**New data capture does not retroactively fill missing fields in old records.** Pre-#5 rows lack `price_change`, `txns_windows`, `fdv_usd`, `age_s`, `mcap_usd` — they will appear as `null` in analysis and must be handled explicitly (the `meme_entry_context.py` analyzer already buckets them as "unobserved").

**Immediately answerable after #5:** Q3 (h6 run-position vs rug outcome).  
**Verified-gap after #5:** Q1/Q2 depend on `dev_mints`/`top10_hold_pct`/`creator_hold_pct` persistence — confirm branch implementation before counting on them.  
**Deferred to #6:** Q5 (bundle cluster share).

---

## 11. Time-gate schedule (derived from window #4 analysis)

**Source:** 102 round-trips, 6.4 days of live window-#4 blotter data.

### Active windows (UTC) — run the satellite ONLY during these hours

| Window | Rug Rate | Net P&L | Sample | Why |
|---|---|---|---|---|
| **16:00–19:00** | **18%** (3/17) | **+$79** | 17 exits | Best pivot/rug ratio; 0 rugs at 16:00 & 19:00; 6 pivots at 16:00 |
| **02:00–07:00** | **29–30%** | **+$64 to +$87** | 14–23 exits | Low rug count; positive expectancy; 04:00–07:00 = 4 rugs / 10 pivots |

**Total active coverage:** 8 hours/day (02:00–07:00 + 16:00–19:00) → dodges ~60% of rugs, keeps ~65% of pivot upside.

### Death zones — hard block entries during these windows

| Window | Rug Rate | Net P&L | Action |
|---|---|---|---|
| **00:00–03:00** | 100% at 00:00/01:00 | –$74 | **HARD BLOCK** — zero pivots 00–01 |
| **07:00–11:00** | 67–75% | –$112 to –$138 | **HARD BLOCK** — 08:00/10:00 are rug magnets |
| 12:00–13:00 | 50–80% | mixed | Soft block (13:00 = 80% rugs) |
| 20:00–23:00 | 58–75% | –$13 to –$56 | Soft block (20:00 = 5 rugs / 3 pivots) |

### Entry-hour policy (what the gate enforces)

| Entry Hour | Rug% | Avg Net | Gate Action |
|---|---|---|---|
| **17:00** | **12.5%** | +$0.90 | **ALLOW** — best hour |
| **19:00** | **0%** | +$4.47 | **ALLOW** — zero rugs |
| **11:00** | **0%** | +$7.32 | **ALLOW** — zero rugs (tiny sample) |
| **03:00–04:00** | 22–25% | +$3 to +$13 | **ALLOW** — positive expectancy |
| **22:00** | 33% | +$11 | **ALLOW** — decent |
| 00:00, 01:00, 07:00, 12:00, 21:00, 23:00 | 100% | negative | **BLOCK** — pure rug traps |

### Implementation note for restart #6+

Add a `TIME_GATE` module to `src/meme_satellite.py` that:
1. Wraps `try_enter()` with an hour-of-day check (UTC)
2. Returns `entry_blocked` with `rejected_by: "time_gate"` + `window: "death_zone"` / `"active_window"`
3. Is configurable via env `TARS_TIME_GATE=1` (OFF by default, ships at #6)
4. Uses the **entry hour** (not exit hour) as the gating moment — per the entry-hour outcome table above

### Latency calibration (for exit logic, not gating)
- **Rug median hold:** 10.3 min — if position survives 15 min, rug probability drops sharply
- **Pivot median hold:** 20.4 min (bimodal) — some scalps, some runners

This schedule is **observational, not predictive** — window #4 data only. Re-validate after each G1 cycle. Do not encode as immutable constants; keep as data-driven policy.

---

## 10. Explicit scope statement

**Restart #5 changes exactly two things, both instrumentation-only:**

1. Blotter rows gain `price_change`, `txns_windows`, `fdv_usd`, `age_s`, `mcap_usd` (entry) and `price_change` (screen) — additive, `getattr`-safe, old rows readable.
2. Confirm stage code ships **unarmed** — measures LP drain, rejects nothing.

**Nothing else changes:** no `MAX_*`/`EXIT_*`/`RUG_*` constants, no `TARS_BUNDLE_SCAN`, no `TARS_BREAKERS`, no sizing, no cooldown, no exit threshold, no filter threshold. Window #4 is not voided; restart #5 opens a fresh G1 clock under one stable rule set.

---

**Status:** Pre-deploy documentation only. Nothing on the VPS has been touched. Nothing has been pushed to the public repo.