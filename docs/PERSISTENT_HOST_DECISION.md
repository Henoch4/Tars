# Persistent Host Decision (Phase 1 Blocker)

**Status**: DECISION REQUIRED — blocks mainnet deployment

---

## The Problem

`vercel.json` serverless deployment contradicts the runtime requirements:

| Component | Serverless Problem | Required |
|-----------|-------------------|----------|
| Multi-leg package state | In-memory, lost on lambda freeze | Persistent in-memory state |
| Risk counters | Temp-dir JSON, lost on cold start | Persistent `RISK_STATE_PATH` |
| `/ws/cycles` WebSocket hub | Lambda can't hold connections | Persistent WebSocket server |
| Multi-leg dispatch | Freeze between legs = naked position | Continuous process |
| `src/scheduler.py` | Cron job, not event-driven | Persistent scheduler |

---

## Options Analysis

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **VPS (Hetzner/Contabo/DigitalOcean)** | Full control, persistent disk, WebSocket support, $4-6/mo | Manual ops, no auto-scale | ✅ **RECOMMENDED** |
| **Railway/Render/Fly.io** | Managed, WebSocket support, persistent volumes | $5-20/mo, less control | ✅ VIABLE |
| **Vercel + External State** | Keep Vercel for HTTP, external Redis/DB for state | Complex, split brain risk | ❌ COMPLEX |
| **AWS ECS/Fargate** | Managed containers, ALB, persistent EFS | Overkill, $15+/mo | ❌ OVERKILL |
| **Self-hosted (home/office)** | Free, full control | Power/internet reliability, no static IP | ❌ UNRELIABLE |

---

## Decision: **Contabo Cloud VPS (€4.50/mo)**

**Rationale**:
- €4.50/mo for 4 vCPU / 8 GB RAM / 100 GB SSD / unlimited traffic
- Germany data center (GDPR, low latency to X Layer Frankfurt)
- Full root access, systemd, persistent SSD
- Unlimited egress (critical for WebSocket hub + OKX polling)
- 99.9% SLA, ISO 27001 certified

**Specs**:
- 4 vCPU AMD EPYC
- 8 GB RAM
- 100 GB NVMe SSD
- 1 Gbps port, unlimited traffic
- DDoS protection included
- Snapshot/backup API

**Location**: Nuremberg, Germany (closest to X Layer Frankfurt RPC)

---

## Deployment Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Contabo VPS (Ubuntu 22.04)               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  systemd    │  │  Redis      │  │  Nginx (reverse     │  │
│  │  services   │  │  (risk      │  │  proxy + TLS)        │  │
│  │  - agent    │  │  counters)  │  │                       │  │
│  │  - scheduler│  │             │  │                       │  │
│  │  - ws hub   │  │             │  │                       │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│                                                             │
│  /data/risk_state/  ← persistent JSONL (RISK_STATE_PATH)   │
│  /data/audit_log/   ← JSONL audit trail                    │
│  /data/learning/    ← learning state store (I12)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │   X Layer RPC       │
                    │  (primary + fallback)│
                    └─────────────────────┘
```

---

## Environment Configuration

```bash
# /etc/tars/agent.env (chmod 600, owned by tars user)
DRY_RUN=false
ALLOW_LIVE=true
AGENT_API_TOKEN=<strong-random-token>
RISK_STATE_PATH=/data/risk_state
AUDIT_LOG_PATH=/data/audit_log
LEARNING_STORE_PATH=/data/learning
XLAYER_RPC_URL=https://rpc.xlayer.tech
XLAYER_RPC_URL_FALLBACK=https://rpc.xlayer.tech
XLAYER_CHAIN_ID=1952
AUDIT_CONTRACT_ADDRESS=0x...
AGENT_WALLET_PRIVATE_KEY=0x...  # rotated, never in git
OKX_API_KEY=...
OKX_SECRET_KEY=...
OKX_PASSPHRASE=...
ALERT_WEBHOOK_URL=https://...
XLAYER_RPC_URL_FALLBACK=https://rpc.xlayer.tech
DRY_RUN=false
AGENT_API_TOKEN=<strong-token>
```

---

## Systemd Service Files

### `/etc/systemd/system/tars-agent.service`
```ini
[Unit]
Description=TARS Trading Agent
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=tars
WorkingDirectory=/opt/tars
EnvironmentFile=/etc/tars/agent.env
ExecStart=/opt/tars/venv/bin/python -m src.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/tars-scheduler.service`
```ini
[Unit]
Description=TARS Trading Scheduler
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=tars
WorkingDirectory=/opt/tars
EnvironmentFile=/etc/tars/agent.env
ExecStart=/opt/tars/venv/bin/python -m src.scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/tars-ws-hub.service`
```ini
[Unit]
Description=TARS WebSocket Hub
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=tars
WorkingDirectory=/opt/tars
EnvironmentFile=/etc/tars/agent.env
ExecStart=/opt/tars/venv/bin/python -m src.ws_hub
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Deployment Checklist

- [ ] Provision Contabo VPS (Ubuntu 22.04)
- [ ] Create `tars` user, add to `docker` group (if needed)
- [ ] Install Python 3.11+, Redis, Nginx, Git
- [ ] Clone repo to `/opt/tars`, create venv, install deps
- [ ] Configure `/etc/tars/agent.env` (chmod 600)
- [ ] Create `/data/{risk_state,audit_log,learning}` dirs, chown tars:tars
- [ ] Install systemd service files, `systemctl daemon-reload`
- [ ] Configure Nginx reverse proxy with TLS (Let's Encrypt)
- [ ] Test: `systemctl start tars-agent tars-scheduler tars-ws-hub`
- [ ] Verify: `/health`, `/api/v1/metrics`, `/ws/cycles` work
- [ ] Configure logrotate for `/var/log/tars/*.log`
- [ ] Add uptime monitoring (UptimeRobot / Healthchecks.io)

---

## Rollback Plan

If VPS fails:
1. DNS failover to backup VPS (pre-provisioned, powered off)
2. Restore `/data` from last snapshot (Contabo snapshot API)
3. Update DNS A record (TTL 60s)
4. RTO < 10 min, RPO < 5 min (snapshot every 5 min)

---

## Cost Summary

| Item | Monthly Cost |
|------|--------------|
| Contabo Cloud VPS (4 vCPU/8GB/100GB) | €4.50 |
| Domain (tars.trade) | ~€10/yr |
| Let's Encrypt TLS | Free |
| Monitoring (UptimeRobot) | Free |
| **Total** | **~€4.50/mo** |

---

## Decision Log

| Date | Decision | Author |
|------|----------|--------|
| 2026-09-04 | Contabo VPS selected over Railway/Fly.io/VPS | @henoch |

---

## Next Steps

1. [ ] Provision Contabo VPS
2. [ ] Run deployment script
3. [ ] Verify all endpoints
3. [ ] Update DNS A record to VPS IP
4. [ ] Test full deposit→trade→withdraw cycle
5. [ ] Update `mainnet-roadmap.md` Phase 1 checklist