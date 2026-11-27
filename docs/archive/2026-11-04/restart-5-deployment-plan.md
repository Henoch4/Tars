# Restart #5 Deployment Plan — Entry-Context Capture as Instrumentation

**Target window:** restart #5 opens at day 7 gate, **2026-10-30 09:09Z**
**VPS:** `root@80.190.82.127`, service `tars-satellite`
**Deploy method:** md5-verified scp + backup + restart (no `git pull` on the box)

---

## 1. Scope statement — this is the ONE change

Restart #5 ships two coordinated pieces as a single instrumentation change:

| Piece | Branch | What it does | Gate impact |
|-------|--------|--------------|-------------|
| **Run-position context capture** | `feat/entry-context` (`f909d30`) | `enrich_mint` returns `price_change {m5,h1,h6,h24}`, `txns_windows`, `fdv_usd` from the same DexScreener response already fetched. Threaded through `TokenSnapshot` → feed → runner → screen + entry blotter rows. | **None.** Verdict-invariance pinned by test (`tests/test_entry_context.py::test_context_never_changes_a_verdict`). Identical snapshot ± context = identical screamer verdict. |
| **Confirmation stage** | `wip/restart-5-confirmation-stage` (`b6deb47`) | `src/meme_lp_velocity.py` velocity verdict gates entry when worst measured LP drain rate crosses 4%/min (≥2 samples required, armed default-OFF). Ships unarmed (`armed=False`) for #5 — measures, does not reject. | **None while unarmed.** Reads only, no veto power. |

Both are **measurement-only instrumentation**. No entry/exit/filter/sizing/sizing rule changes. Window #4 remains valid; restart #5 opens a fresh G1 clock under one stable rule set.

**Not in #5:** `TARS_BUNDLE_SCAN` stays OFF. `TARS_BREAKERS` stays OFF. Kill-switch durable state (`feat/durable-breakers`, `3a627f0`) is built but undeployed — ships at its own declared restart, not here.

---

## 2. Branches and base state

Both branches are local-only, unpushed, stacked on `main`:

```
main                          (current trunk, window #4 rules)
├── feat/entry-context        f909d30  (capture only, verdict-invariance pinned)
└── wip/restart-5-confirmation-stage  b6deb47  (confirm stage, default OFF)
```

**Pre-deploy requirement:** `wip/restart-5-confirmation-stage` must be rebased onto current `main` before the deploy commit is assembled. Verify with:

```bash
git checkout main
git pull --ff-only        # only if origin is trusted and clean; otherwise verify manually
git checkout wip/restart-5-confirmation-stage
git rebase main
git checkout feat/entry-context
git rebase main
```

**Do not push.** The deploy commits are assembled locally and copied to the VPS via scp. The public repo stays unchanged until the operator explicitly approves a later push/PR.

---

## 3. Exact files that ship

These are the files that differ between the pre-#5 state and post-#5 state. md5 hashes are computed at deploy time and recorded in step 4.

### From `feat/entry-context` (`f909d30`):

| File | Role |
|------|------|
| `src/meme_discovery.py` | `enrich_mint` parses `price_change`/`txns_windows`/`fdv_usd`; `_fnum` None-safe reader; latent crash fix (non-dict `txns` no longer kills enrichment) |
| `src/meme_satellite.py` | `TokenSnapshot` gains `price_change`, `txns_windows`, `fdv_usd` (default `None`) |
| `src/meme_feed.py` | `snapshot_for_mint` passes the three fields through from `extra` |
| `scripts/meme_runner.py` | `_enrich` threads `price_change`/`txns_windows`/`fdv_usd` into `extra` dict |
| `src/meme_loop.py` | `screen_candidates` and `try_enter` record the three fields + `age_s`/`mcap_usd` via `getattr` (additive, old rows readable) |
| `scripts/meme_entry_context.py` | Offline analyzer: joins entries→exits, buckets by h6 run-position |
| `tests/test_entry_context.py` | 15 tests: parse/None-safety, snapshot passthrough, verdict invariance, blotter rows, analyzer join/bucketing |

### From `wip/restart-5-confirmation-stage` (`b6deb47`, rebased):

| File | Role |
|------|------|
| `src/meme_lp_velocity.py` | Velocity verdict engine (already merged; confirm stage reads it) |
| `src/meme_loop.py` | `SatelliteLoop` accept `breakers=`/`on_entry=`/`on_exit=` constructor params (default `None`, inert) |
| `src/meme_confirm_stage.py` (new) | Confirm-stage entry gate: requires ≥2 LP samples, rejects when worst drain ≥4%/min when armed |
| `scripts/meme_runner.py` | Wires confirm stage into entry path, default OFF |
| `tests/test_meme_confirm_stage.py` | 212 tests: default-OFF inert, armed reject, insufficient-data pass, restart durability |

### Files intentionally NOT touched in #5:

- `src/meme_discovery.py` enrichment logic beyond context parse — no filter threshold changes
- `TARS_BUNDLE_SCAN` / `src/meme_bundle.py` — stays OFF
- `TARS_BREAKERS` / `src/satellite_breakers.py` — stays OFF
- Any `MAX_*` or `EXIT_*` constant in `src/meme_satellite.py`
- `G1_CLOCK_RESTART.json` — continuity preserved, not reset manually

---

## 4. Pre-deploy: assemble and md5-record

Run on the **local machine**, before any scp:

```bash
# 1. Verify clean tree on main
git status --short   # must be empty
git log --oneline -1  # note the base commit

# 2. Rebase confirm-stage onto main (if not already done)
git checkout wip/restart-5-confirmation-stage
git rebase main

# 3. Create the deploy commit (local only)
git checkout -b deploy/restart-5-confirmation-stage
git merge --no-ff feat/entry-context
git merge --no-ff wip/restart-5-confirmation-stage
git commit -m "deploy: restart #5 entry-context capture + confirm stage (instrumentation only)"

# 4. Record md5 of every file that will ship
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

---

## 5. Pre-deploy backup on VSS (run on the VPS)

SSH into the VPS and back up the live satellite state **before** anything is copied:

```bash
ssh root@80.190.82.127

TS=$(date +%Y%m%d%H%M%S)
BACKUP_DIR="/opt/tars/backup/restart-5-pre-${TS}"
mkdir -p "${BACKUP_DIR}"

# Copy (not move) the live state — originals stay in place
cp -a /opt/tars/data/meme_satellite/blotter.jsonl      "${BACKUP_DIR}/"
cp -a /opt/tars/data/meme_satellite/positions.json     "${BACKUP_DIR}/"
cp -a /opt/tars/data/meme_satellite/G1_CLOCK_RESTART.json "${BACKUP_DIR}/"
cp -a /opt/tars/data/meme_satellite/deployer_history.json "${BACKUP_DIR}/"
cp -a /opt/tars/data/meme_satellite/HALT               "${BACKUP_DIR}/" 2>/dev/null || true

# Record the running service state
systemctl status tars-satellite --no-pager > "${BACKUP_DIR}/service-status.pre.txt"

# Verify backups
md5sum "${BACKUP_DIR}"/* > "${BACKUP_DIR}/md5.pre.txt"
ls -la "${BACKUP_DIR}"
```

Record `${BACKUP_DIR}` path — it is the rollback anchor if anything goes wrong.

---

## 6. Deploy: md5-verified scp to VPS

From the **local machine**, copy each file individually and verify md5 after each copy:

```bash
# From the deploy branch working tree
VPS="root@80.190.82.127"
REMOTE_DIR="/opt/tars/src"   # or wherever src/ lives on the VPS — verify path

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

# Also copy the md5 record for post-deploy verification
scp deploy/restart-5-md5.txt "${VPS}:${REMOTE_DIR}/../deploy/restart-5-md5.txt"
```

**After scp, on the VPS:** re-run md5 against the local record and confirm every hash matches. If any hash differs, **abort** — do not restart.

```bash
ssh root@80.190.82.127
cd /opt/tars
md5sum -c deploy/restart-5-md5.txt   # all must print "OK"
```

---

## 7. Restart sequence (single change)

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
- `G1_CLOCK_RESTART.json` is still present and readable

**Do not** toggle `TARS_BREAKERS`, `TARS_BUNDLE_SCAN`, or any env var in this restart. Those are separate declared changes for later restarts.

---

## 8. Post-restart verification checklist

Run after the first 2–3 cycles:

| Check | Command / Method | Pass criterion |
|-------|------------------|----------------|
| Blotter gains context fields | `tail -3 /opt/tars/data/meme_satellite/blotter.jsonl` | Entry rows carry `price_change`, `txns_windows`, `fdv_usd` |
| Mine script runs clean | `python scripts/meme_blotter_mine.py <path>` | Exits 0, prints class distribution + gap report |
| No verdict change | Compare screen `rejected_by` mix pre/post | Same distribution (unconfirmed dominant, no new codes) |
| G1 clock intact | `cat /opt/tars/data/meme_satellite/G1_CLOCK_RESTART.json` | Timestamp unchanged, window continues |
| No mid-window rule change | `git diff` on VPS vs deploy commit | Only the intended files changed; no `MAX_*`/`EXIT_*` tweaks |
| Service healthy | `systemctl status tars-satellite` | active (running), no crash loops |

---

## 9. Rollback (if anything fails)

If post-restart verification fails at any point:

```bash
ssh root@80.190.82.127
systemctl stop tars-satellite

# Restore backed-up state
BACKUP_DIR=/opt/tars/backup/restart-5-pre-<TS>
cp -a "${BACKUP_DIR}/blotter.jsonl" /opt/tars/data/meme_satellite/
cp -a "${BACKUP_DIR}/positions.json" /opt/tars/data/meme_satellite/
cp -a "${BACKUP_DIR}/G1_CLOCK_RESTART.json" /opt/tars/data/meme_satellite/

# Restore pre-deploy code (reverse the scp — re-copy pre-#5 files from a known-good source)
# ... then restart
systemctl start tars-satellite
```

Rollback restores the exact pre-#5 state; Window #4's data integrity is preserved because the backup was taken before any copy.

---

## 10. Explicit "only change" statement

**Restart #5 changes exactly two things, both instrumentation-only:**

1. Blotter rows gain `price_change`, `txns_windows`, `fdv_usd`, `age_s`, `mcap_usd` (entry) and the same context fields on screen rows (additive, `getattr`-safe, old rows readable).
2. Confirm stage code ships **unarmed** — it measures LP drain but does not reject any entry.

**Nothing else changes:** no `MAX_*`/`EXIT_*`/`RUG_*` constants, no `TARS_BUNDLE_SCAN`, no `TARS_BREAKERS`, no sizing, no cooldown, no exit threshold, no filter threshold. Window #4 is not voided by this deploy; restart #5 opens a fresh G1 clock under one stable rule set.

---

## 11. After #5 lands

- Run `scripts/meme_entry_context.py` against the growing blotter to answer Q1–Q3 (dev_mints clustering, concentration differential, h6 run-position vs rug outcome).
- Restart #6 (next declared change) flips `TARS_BUNDLE_SCAN=1` and covers coverage bars C1–C4.
- The day-7 report (`scripts/meme_g1_report.py --attribution`) runs at the Oct 30 gate and its verdict is appended to `LIVE_TRADING_PLAYBOOK.md` §12.

---

**Prepared:** pre-deploy documentation only. Nothing on the VPS has been touched. Nothing has been pushed to the public repo.
