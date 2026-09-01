#!/bin/bash
# TARS VPS Quick Install Script
# Run as root on fresh Ubuntu 22.04/24.04 VPS

set -euo pipefail

echo "=== TARS VPS Install ==="

# 1. System prep
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip git curl ufw fail2ban nodejs npm

# 2. Create tars user
if ! id "tars" &>/dev/null; then
    useradd -m -s /bin/bash tars
    usermod -aG sudo tars
    echo "tars user created. Add your SSH key to /home/tars/.ssh/authorized_keys"
fi

# 3. Firewall
ufw allow 22/tcp
ufw --force enable

# 4. Fail2ban
systemctl enable fail2ban --now

# 5. Install OKX CLI globally (as tars user for npm global)
sudo -u tars npm install -g @okx_ai/okx-trade-cli

# 6. Clone repo (as tars)
sudo -u tars bash -c '
    cd /opt
    git clone https://github.com/Henoch4/Tars.git tars
    cd tars
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
'

# 7. Install systemd service
cp /opt/tars/deploy/tars-scheduler.service /etc/systemd/system/
systemctl daemon-reload

# 8. Create .env from template
if [ ! -f /opt/tars/.env ]; then
    cp /opt/tars/deploy/.env.template /opt/tars/.env
    chown tars:tars /opt/tars/.env
    echo "Created /opt/tars/.env from template — EDIT IT with your API keys!"
fi

# 9. Set ownership
chown -R tars:tars /opt/tars

echo ""
echo "=== NEXT STEPS ==="
echo "1. Edit /opt/tars/.env with your OKX API credentials"
echo "2. Add VPS IP to OKX API key Trusted IPs"
echo "3. Run: okx auth login  (as tars user)"
echo "4. Start service: systemctl enable tars-scheduler --now"
echo "5. Watch logs: journalctl -u tars-scheduler -f"
echo ""
echo "Install complete. Service is NOT started yet (needs .env config)."