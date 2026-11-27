# R4 window #1 — deploy fpz wiring, flag OFF

**Declared window:** 2026-11-02 08:00 UTC (09:00 WAT). Weekday business hours so a
rollback has daylight coverage. Operator-confirmed.
**Scope:** this window deploys the R4 code with `FPZ_LIVE_ENABLED` unset (= `false`).
It does **not** flip the flag, does not need the paper-cohort verdict, and does not
need the SafetyNet or `client_oid` fixes. The flip (§1.7) is a **separate** window
per spec §9.

**Risk:** the deploy is inert. `fpz_live_enabled=False` routes `decision = ensemble`,
so trading behavior is unchanged. The real risk in this window is operational, not
behavioral: a service that fails to start.

---

## 0. Facts verified on 2026-11-01 (pre-window)

Run by the operator's agent against `root@80.190.82.127`, read-only:

| Check | Result |
|---|---|
| SSH key auth (`BatchMode`) | works; host key already in `known_hosts` |
| `tars-agent` | `active` (uvicorn `src.main:app`, `EnvironmentFile=/etc/tars/agent.env`) |
| `tars-scheduler` | `active` (`python -m src.scheduler`, same env file) |
| `tars-satellite` | `inactive` — stopped in R3, leave it alone |
| `tars-paperz` | `active` — runs from **`/opt/tars/paperz`**, not `/opt/tars` |
| `DRY_RUN` in `/etc/tars/agent.env` | **present**, `true` → scheduler boot validation passes |
| `ALLOW_LIVE` | `false` |
| `FPZ_LIVE_ENABLED` / `FPZ_SOL_ONLY` | **absent** → code defaults (`false` / `true`) apply |
| Deployed tree | `/opt/tars` git repo at `d1ec0a0`, working tree modified (scp-deployed files) |
| Python | `/opt/tars/venv/bin/python` → `python3.12` |

Two consequences:

1. **The scheduler's new boot validation is not a risk today** — `DRY_RUN` is already
   explicit (`true`), so `ALLOW_LIVE=true`-with-defaulted-`DRY_RUN` cannot fire. Re-check
   anyway (step 1) in case the env file changed overnight.
2. **The paper cohort is isolated.** `tars-paperz` runs `/opt/tars/paperz/scripts/paper_trade_sol.py`
   from its own directory. Deploying into `/opt/tars` cannot touch the verdict source.
   Do not restart `tars-paperz` in this window; it is accruing the cohort.

---

## 1. Pre-flight (before anything changes)

```bash
# 1a. boot-validation precondition (THE check that can refuse startup)
grep -E '^(DRY_RUN|ALLOW_LIVE)=' /etc/tars/agent.env
#    REQUIRE: DRY_RUN appears explicitly (true OR false). If ALLOW_LIVE=true and
#    DRY_RUN is absent, STOP — fix the env file first or the scheduler will not start.

# 1b. current state, for the record
systemctl is-active tars-agent tars-scheduler tars-paperz
date -u

# 1c. cohort must still be running (do not disturb it)
systemctl is-active tars-paperz    # expect: active
```

**STOP conditions:** `DRY_RUN` absent while `ALLOW_LIVE=true`; either `tars-agent` or
`tars-scheduler` not `active` before we start (we would be debugging a pre-existing
outage, not this deploy); `tars-paperz` not active.

---

## 2. Backup + checksums (on the VPS)

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=/opt/tars/backup-r4-$TS
mkdir -p "$BK"
cd /opt/tars
for f in src/settings.py src/agent.py src/main.py src/scheduler.py; do
  install -D "$f" "$BK/$f"
  md5sum "$f" | tee -a "$BK/before.md5"
done
echo "$BK" > /tmp/r4_backup_dir
ls -R "$BK"
```

`/opt/tars/backup-r4-<ts>/` is the rollback source. Keep `before.md5` — it is the
pre-change fingerprint of exactly the four files.

---

## 3. Ship (md5-verified scp, R3 discipline)

Payload is **four files only**: they are the complete R4 runtime change.

| Local file | Why |
|---|---|
| `src/settings.py` | six `fpz_*` knobs |
| `src/agent.py` | wiring: scope/arb/decision/audit |
| `src/main.py` | passes knobs into the API-process agent |
| `src/scheduler.py` | passes knobs into the **live-loop** agent; adds boot-error log |

Not shipped: `tests/` (not used on the VPS), `.env.example` (documentation), docs.

```bash
# from the operator workstation, repo root (PowerShell — this host is Windows)
$files = @("src/settings.py","src/agent.py","src/main.py","src/scheduler.py")
foreach ($f in $files) { scp -q $f "root@80.190.82.127:/opt/tars/$f" }
```

Verify, and **stop if any line mismatches** (PowerShell; do not pipeline through
`tail`/`Select-Object -Last` anywhere a step's exit code is the signal — that turns a
failure into a green):

```powershell
$files = @("src/settings.py","src/agent.py","src/main.py","src/scheduler.py")
$bad = 0
foreach ($f in $files) {
  $local  = (Get-FileHash $f -Algorithm MD5).Hash.ToLower()
  $remote = ssh root@80.190.82.127 "md5sum /opt/tars/$f" | ForEach-Object { ($_ -split '\s+')[0] }
  if ($local -ne $remote) { Write-Host "MISMATCH  $f  local=$local remote=$remote"; $bad++ }
  else { Write-Host "ok  $f  $local" }
}
if ($bad -gt 0) { Write-Host "STOP — restore from the backup dir, do not restart" }
```

---

## 4. Import smoke test — BEFORE restarting anything

A syntax/import error must be caught here, not as a crash-loop. Proven on the live box
on 2026-11-01: all four imports succeed there today; the only failure was the expected
`AttributeError: 'Settings' object has no attribute 'fpz_live_enabled'` (the knob is not
deployed yet). After step 3 that attribute must exist — if it does not, the scp did not
land and you must not restart.

```bash
ssh root@80.190.82.127 'cd /opt/tars && ./venv/bin/python -c "
import src.settings as s, src.agent, src.main, src.scheduler as sc
print(\"fpz_defaults:\", s.Settings().fpz_live_enabled, s.Settings().fpz_bypass_ensemble)
print(\"agent_wiring:\", hasattr(src.agent.AutonomousTradingAgent, \"_fpz_decision_signal\"))
print(\"scheduler_settings:\", hasattr(sc, \"get_settings\"))
"; echo "SMOKE_EXIT=$?"'
```

**Required before proceeding** — all three must hold: `fpz_defaults: False True`,
`agent_wiring: True`, `scheduler_settings: True`, and `SMOKE_EXIT=0`.

Do **not** pipe this through `tail`/`head`: the pipeline's exit code is the last
command's, so a python traceback would report success. `SMOKE_EXIT` is the check.

Note: importing `src.main` constructs module-level singletons. If it fails here it
would also have failed at restart; that is exactly what this step is for.

---

## 5. Restart and verify

Restart is a stop/start, so a cycle in flight at that moment is abandoned. With
`DRY_RUN=true` and the flag off there is no market effect — but do not restart
back-to-back "to be sure", because each restart abandons whatever cycle had started.

```bash
ssh root@80.190.82.127 '
systemctl restart tars-agent tars-scheduler
sleep 20
echo "== active states =="
systemctl is-active tars-agent tars-scheduler
echo "== restart counters (must not climb on re-check) =="
systemctl show -p NRestarts --value tars-agent tars-scheduler
echo "== recent errors =="
journalctl -u tars-agent -u tars-scheduler -n 30 --no-pager | grep -iE "traceback|error|critical|failed" || echo "no error lines"
'
```

Then, **10-15 seconds later, a second `NRestarts` sample**. If a counter climbed, the
unit is in the `activating (auto-restart)` loop seen in R3 — **roll back immediately**;
do not "give it a minute".

```bash
sleep 15
ssh root@80.190.82.127 'systemctl show -p NRestarts --value tars-agent tars-scheduler'
```

Also confirm the scheduler is actually cycling rather than merely alive:

```bash
ssh root@80.190.82.127 'journalctl -u tars-scheduler -n 15 --no-pager; ls -l /opt/tars/data/loop_heartbeat.json'
```

And confirm the live loop **read the knobs as off** — the config is inert, proven from
the running process, not from the file:

```bash
ssh root@80.190.82.127 'cd /opt/tars && ./venv/bin/python -c "
import src.settings as s; st=s.Settings()
print(\"fpz_live_enabled =\", st.fpz_live_enabled, \"| arb_priority =\", st.fpz_arb_package_priority)
"'
# expect: fpz_live_enabled = False | arb_priority = False
```

---

## 6. Rollback (any time; no partial state)

```bash
ssh root@80.190.82.127 '
BK=$(cat /tmp/r4_backup_dir)
cd /opt/tars
for f in src/settings.py src/agent.py src/main.py src/scheduler.py; do
  cp -p "$BK/$f" "$f"
done
md5sum -c "$BK/before.md5"
systemctl restart tars-agent tars-scheduler
sleep 15
systemctl is-active tars-agent tars-scheduler
'
```

Rollback is a file restore + restart. `tars-paperz` is untouched throughout, so the
cohort verdict survives a bad deploy — which is the point of the separate window.

---

## 7. Explicitly NOT in this window

- No `FPZ_LIVE_ENABLED=true`. Not in the env file, not in a `systemctl set-environment`,
  not anywhere. The flip is its own window after a PASS verdict (§1.6/§1.7).
- No `tars-paperz` restart, no `paper_trade_sol.py` edit — it is the verdict source.
- No `tars-satellite` start (R3 stopped it deliberately).
- No SafetyNet or `client_oid` fix. Both are go-live blockers for the *flip*, tracked in
  spec §12/§12.1, and neither changes behavior with the flag off.

---

## 8. Post-window record

Append to spec §11: window timestamp, md5 verdict, `NRestarts` before/after, the
`fpz_live_enabled=False` proof from the running process, and any deviation. If the
window is aborted, record why — an aborted window is not a silent one.
