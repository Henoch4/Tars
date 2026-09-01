# TARS VPS Deployment Checklist

## 1. Provision VPS
- [ ] Provider: DigitalOcean / Linode / Vultr / Hetzner (recommended: $6-12/mo, 1-2 vCPU, 1-2GB RAM, Ubuntu 22.04/24.04)
- [ ] Region: Close to OKX servers (Singapore, Tokyo, or US East)
- [ ] Static IPv4 assigned (note it for OKX IP whitelist)
- [ ] SSH key added (disable password auth)
- [ ] Firewall: Allow SSH (22), optionally 80/443 if running dashboard

## 2. Initial Server Setup
```bash
# On VPS as root
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip git curl ufw fail2ban

# Create tars user
useradd -m -s /bin/bash tars
usermod -aG sudo tars
# Copy your SSH key to /home/tars/.ssh/authorized_keys

# Firewall
ufw allow 22/tcp
ufw enable

# Fail2ban
systemctl enable fail2ban --now
```

## 3. Clone & Install
```bash
# As tars user
cd /opt
git clone https://github.com/Henoch4/Tars.git tars
cd tars

# Python venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Install OKX CLI globally (for okx_cli.py)
npm install -g @okx_ai/okx-trade-cli
okx auth login  # or configure API-key profile
```

## 4. Configure Environment
```bash
cp deploy/.env.template .env
# Edit .env with your values:
# - OKX_API_KEY, OKX_API_SECRET, OKX_API_PASSPHRASE
# - DRY_RUN=true (start with dry-run!)
# - SCHEDULER_INTERVAL_MINUTES=15
# - AGENT_ID=tars-scheduler-001
```

## 5. Whitelist VPS IP on OKX
- Go to https://www.okx.com/account/trade/api
- Edit your API key → Trusted IP addresses
- Add VPS static IPv4
- Save

## 6. Install systemd Service
```bash
sudo cp deploy/tars-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tars-scheduler --now
sudo systemctl status tars-scheduler
```

## 7. Verify
```bash
# Check logs
sudo journalctl -u tars-scheduler -f

# Should see cycle logs every 15 min
# First run: DRY_RUN=true, signals only, no orders
```

## 8. Monitoring (Optional but Recommended)
- [ ] Set up log rotation: `/etc/logrotate.d/tars-scheduler`
- [ ] Add health check endpoint (if running dashboard)
- [ ] Configure alerting: failed cycles, risk gate killswitch, disk space
- [ ] Backup strategy for audit logs / on-chain data

## 9. Go Live (When Ready)
- [ ] Test DRY_RUN for 24-48 hours, verify signals/logic
- [ ] Audit on-chain contract parameters (risk params, mandates)
- [ ] Change DRY_RUN=false in .env
- [ ] Restart service: `sudo systemctl restart tars-scheduler`
- [ ] Monitor first live cycles closely

## 10. Rollback Plan
- [ ] Keep previous commit hash for quick rollback
- [ ] `git checkout <prev-hash> && sudo systemctl restart tars-scheduler`
- [ ] Document any manual interventions needed

---

## Quick Commands Reference
```bash
# View live logs
sudo journalctl -u tars-scheduler -f

# Restart after config change
sudo systemctl restart tars-scheduler

# Stop for maintenance
sudo systemctl stop tars-scheduler

# Check status
sudo systemctl status tars-scheduler

# Manual one-off cycle (dry-run)
cd /opt/tars && source .venv/bin/activate && python -m src.scheduler --once --dry-run

# Manual one-off cycle (LIVE)
cd /opt/tars && source .venv/bin/activate && DRY_RUN=false python -m src.scheduler --once
```

## Support Files in Repo
- `deploy/tars-scheduler.service` — systemd unit
- `deploy/.env.template` — environment template
- `src/scheduler.py` — main scheduler module
- `src/agent.py` — trading agent
- `src/execution/risk_gate.py` — risk controls