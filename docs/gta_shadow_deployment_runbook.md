# GTA shadow deployment runbook

This deploys only the read-only GTA discovery scanner. It must remain paper-only.

## Preconditions

- A VPS change window has been declared and confirmed by the operator.
- `/etc/tars/agent.env` keeps `DRY_RUN=true` and `ALLOW_LIVE=false`.
- `config/gta_event.yaml` keeps both `enabled: false` and `live_enabled: false`.
- Existing services are not changed.

## Files shipped

- `deploy/tars-gta-shadow.service` → `/etc/systemd/system/tars-gta-shadow.service`
- `deploy/tars-gta-shadow.timer` → `/etc/systemd/system/tars-gta-shadow.timer`
- `deploy/tars-gta-shadow-stop.service` → `/etc/systemd/system/tars-gta-shadow-stop.service`
- `deploy/tars-gta-shadow-stop.timer` → `/etc/systemd/system/tars-gta-shadow-stop.timer`
- `scripts/gta_shadow.py` → `/opt/tars/scripts/gta_shadow.py`
- `src/gta_event.py`, `src/gta_enrichment.py`, `src/gta_discovery.py` → `/opt/tars/src/`
- `config/gta_event.yaml` → `/opt/tars/config/gta_event.yaml`

The fixed observation window is 14 Nov 22:00 → 17 Nov 22:00 Lagos time. The service writes only to `/opt/tars/data/gta_event/shadow_observations.jsonl`. Do **not** copy
`gta_live_executor.py`, `gta_live_runner.py`, or any wallet/signing module for this deployment.

## Operator commands (inside the approved window)

```bash
systemctl daemon-reload
systemctl enable tars-gta-shadow.service
systemctl start tars-gta-shadow.service
systemctl status tars-gta-shadow.service --no-pager
journalctl -u tars-gta-shadow.service -n 50 --no-pager
```

Stop and disable it if preflight or logs are unexpected:

```bash
systemctl stop tars-gta-shadow.service
systemctl disable tars-gta-shadow.service
```

## Shadow acceptance checks

Observe for a meaningful period and record enrichment completeness, rejection reasons, discovery volume, reconciliation health, simulated exit decisions, and absence of broadcast attempts. Any RPC, parse, or state error must fail closed and keep live flags off.

Live authorization remains a separate decision and is not part of this runbook.
