#!/usr/bin/env bash
# TARS Deployment Script for Contabo VPS
# Run as root on fresh Ubuntu 22.04

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Configuration
TARS_USER="tars"
TARS_HOME="/opt/tars"
TARS_REPO="https://github.com/Henoch4/Tars.git"
TARS_BRANCH="main"
PYTHON_VERSION="3.11"

log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')] $*${NC}"
}

warn() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: $*${NC}"
}

error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*${NC}"
    exit 1
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root"
    fi
}

install_system_packages() {
    log "Installing system packages..."
    apt-get update
    apt-get install -y \
        python3.11 python3.11-venv python3.11-dev \
        python3-pip \
        git \
        curl \
        wget \
        gnupg \
        lsb-release \
        ca-certificates \
        software-properties-common \
        redis-server \
        nginx \
        certbot \
        python3-certbot-nginx \
        ufw \
        fail2ban \
        logrotate \
        htop \
        tmux \
        git
}

create_tars_user() {
    log "Creating tars user..."
    if ! id "$TARS_USER" &>/dev/null; then
        useradd -r -m -d "$TARS_HOME" -s /bin/bash "$TARS_USER"
        usermod -aG sudo "$TARS_USER"
    fi
}

setup_directories() {
    log "Setting up directories..."
    mkdir -p /data/risk_state
    mkdir -p /data/audit_log
    mkdir -p /data/learning
    mkdir -p /var/log/tars
    mkdir -p /etc/tars
    
    chown -R "$TARS_USER:$TARS_USER" /data /var/log/tars
    chmod 750 /data /var/log/tars
}

install_python() {
    log "Setting up Python environment..."
    cd "$TARS_HOME"
    
    # Create virtual environment
    sudo -u "$TARS_USER" python3.11 -m venv venv
    
    # Upgrade pip
    sudo -u "$TARS_USER" "$TARS_HOME/venv/bin/pip" install --upgrade pip setuptools wheel
    
    # Install dependencies
    sudo -u "$TARS_USER" "$TARS_HOME/venv/bin/pip" install --upgrade pip
    sudo -u "$TARS_USER" "$TARS_HOME/venv/bin/pip" install -r requirements.txt
    
    # Install optional ML dependencies
    sudo -u "$TARS_USER" "$TARS_HOME/venv/bin/pip" install \
        lightgbm \
        scikit-learn \
        pandas \
        numpy \
        torch \
        transformers \
        accelerate \
        peft \
        bitsandbytes \
        || warn "Some ML dependencies failed to install (expected on CPU-only)"
}

clone_repo() {
    log "Cloning repository..."
    if [[ -d "$TARS_HOME/.git" ]]; then
        log "Repository exists, pulling latest..."
        cd "$TARS_HOME"
        sudo -u "$TARS_USER" git fetch origin
        sudo -u "$TARS_USER" git checkout "$TARS_BRANCH"
        sudo -u "$TARS_USER" git pull origin "$TARS_BRANCH"
    else
        sudo -u "$TARS_USER" git clone -b "$TARS_BRANCH" "$TARS_REPO" "$TARS_HOME"
    fi
}

setup_env_file() {
    log "Setting up environment file..."
    ENV_FILE="/etc/tars/agent.env"
    
    if [[ ! -f "$ENV_FILE" ]]; then
        cat > "$ENV_FILE" <<'EOF'
# TARS Agent Configuration
# Generated: $(date -u +"%Y-%m-%d %H:%M:%S UTC")

# Trading Mode
DRY_RUN=false
ALLOW_LIVE=true

# Agent Identity
AGENT_WALLET_PRIVATE_KEY=
AGENT_API_TOKEN=

# OKX Credentials
OKX_API_KEY=
OKX_SECRET_KEY=
OKX_PASSPHRASE=
OKX_BASE_URL=https://www.okx.com
OKX_DEMO=false

# X Layer Configuration
XLAYER_RPC_URL=https://rpc.xlayer.tech
XLAYER_RPC_URL_FALLBACK=https://rpc.xlayer.tech
XLAYER_CHAIN_ID=1952
AUDIT_CONTRACT_ADDRESS=

# Risk Parameters
MAX_POSITION_USD=5000
MAX_DAILY_LOSS_USD=500
MAX_DAILY_TRADES=10
MAX_DAILY_VOLUME_USD=50000
MAX_LEVERAGE=5.0
MIN_CONFIDENCE_BPS=7000
MAX_PRICE_AGE_SECONDS=60

# Risk Gate Configuration
REGIME_THROTTLE=false
REGIME_BAND_PCT=5.0
REGIME_SIZE_SCALE=0.8
REGIME_BUFFER=20

# Funding Arb
FUNDING_ARB_MIN_RATE=0.001
CARRY_TAKER_FEE_BPS_SPOT=5.0
CARRY_TAKER_FEE_BPS_PERP=5.0
CARRY_SLIPPAGE_BPS=3.0
CARRY_HOLD_PERIODS=21

# ML Gate
USE_ML_CARRY_GATE=false
LOSS_COOLDOWN_MINUTES=30
DRAWDOWN_WINDOW_DAYS=3
DRAWDOWN_LOSS_MULT=2.0

# Vault Configuration
VAULT_MIN_DEPOSIT=10
VAULT_MAX_TVL=100000
VAULT_MIN_DEPOSIT_USD=5
VAULT_MAX_TVL_USD=100000

# x402 Payment
PAY_TO_ADDRESS=
X402_MAX_USD_PER_CALL=5.00
X402_PREMIUM_PER_MIN=10
X402_MICRO_PER_MIN=120

# Monitoring
ALERT_WEBHOOK_URL=

# Logging
LOG_LEVEL=INFO
EOF
    
    chmod 600 "$ENV_FILE"
    chown root:root "$ENV_FILE"
    log "Environment file created at $ENV_FILE"
    warn "IMPORTANT: Edit $ENV_FILE and fill in all required values before starting services!"
}

setup_systemd_services() {
    log "Setting up systemd services..."
    
    # Agent service
    cat > /etc/systemd/system/tars-agent.service <<'EOF'
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
EOF

    # Scheduler service
    cat > /etc/systemd/system/tars-scheduler.service <<'EOF'
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
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # WebSocket hub service
    cat > /etc/systemd/system/tars-ws-hub.service <<'EOF'
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
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # Redis service (if not using system redis)
    cat > /etc/systemd/system/tars-redis.service <<'EOF'
[Unit]
Description=TARS Redis Instance
After=network.target

[Service]
Type=simple
User=tars
ExecStart=/usr/bin/redis-server /etc/redis/tars.conf
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    # Redis config
    mkdir -p /etc/redis
    cat > /etc/redis/tars.conf <<'EOF'
bind 127.0.0.1
port 6379
maxmemory 256mb
maxmemory-policy allkeys-lru
save 900 1
save 300 10
save 60 10000
appendonly yes
appendfsync everysec
EOF

    systemctl daemon-reload
    systemctl enable redis-server
    systemctl enable tars-agent tars-scheduler tars-ws-hub
    
    log "Systemd services configured"
}

setup_nginx() {
    log "Configuring Nginx reverse proxy..."
    
    cat > /etc/nginx/sites-available/tars <<'EOF'
server {
    listen 80;
    server_name _;
    
    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name _;
    
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    
    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    
    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/s;
    limit_req zone=api burst=50 nodelay;
    
    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    # WebSocket
    location /ws/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
    
    # API endpoints
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # Static files
    location /static/ {
        alias /opt/tars/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF

    # Enable site
    ln -sf /etc/nginx/sites-available/tars /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default
    
    # Test and reload
    nginx -t && systemctl reload nginx
    log "Nginx configured"
}

setup_firewall() {
    log "Configuring firewall..."
    ufw --force enable
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow ssh
    ufw allow http
    ufw allow https
    ufw allow from 127.0.0.1 to any port 6379  # Redis
    ufw allow from 127.0.0.1 to any port 8000  # Agent API
    ufw allow from 127.0.0.1 to any port 8001  # WebSocket hub
    ufw --force reload
    log "Firewall configured"
}

setup_logrotate() {
    log "Configuring logrotate..."
    cat > /etc/logrotate.d/tars <<'EOF'
/var/log/tars/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 640 tars tars
    sharedscripts
    postrotate
        systemctl reload tars-agent tars-scheduler tars-ws-hub > /dev/null 2>&1 || true
    endscript
}
EOF
    log "Logrotate configured"
}

setup_monitoring() {
    log "Setting up basic monitoring..."
    
    # Create health check script
    cat > /opt/tars/health_check.sh <<'EOF'
#!/bin/bash
# Health check for TARS services

check_service() {
    if systemctl is-active --quiet "$1"; then
        echo "OK: $1 is running"
        return 0
    else
        echo "FAIL: $1 is NOT running"
        return 1
    fi
}

check_port() {
    if nc -z localhost "$2" 2>/dev/null; then
        echo "OK: Port $2 ($1) is open"
        return 0
    else
        echo "FAIL: Port $2 ($1) is closed"
        return 1
    fi
}

FAILURES=0

check_service "tars-agent" || FAILURES=$((FAILURES + 1))
check_service "tars-scheduler" || FAILURES=$((FAILURES + 1))
check_service "tars-ws-hub" || FAILURES=$((FAILURES + 1))
check_service "redis-server" || FAILURES=$((FAILURES + 1))
check_service "nginx" || FAILURES=$((FAILURES + 1))

check_port "Agent API" 8000 || FAILURES=$((FAILURES + 1))
check_port "WebSocket Hub" 8001 || FAILURES=$((FAILURES + 1))
check_port "Redis" 6379 || FAILURES=$((FAILURES + 1))
check_port "Nginx HTTP" 80 || FAILURES=$((FAILURES + 1))
check_port "Nginx HTTPS" 443 || FAILURES=$((FAILURES + 1))

# Check disk space
DISK_USAGE=$(df /data | awk 'NR==2 {print $5}' | sed 's/%//')
if [[ $DISK_USAGE -gt 80 ]]; then
    echo "WARN: Disk usage at ${DISK_USAGE}%"
    FAILURES=$((FAILURES + 1))
fi

# Check memory
MEM_USAGE=$(free | awk '/Mem:/ {printf "%.0f", $3/$2 * 100}')
if [[ $MEM_USAGE -gt 90 ]]; then
    echo "WARN: Memory usage at ${MEM_USAGE}%"
    FAILURES=$((FAILURES + 1))
fi

if [[ $FAILURES -gt 0 ]]; then
    echo "HEALTH CHECK FAILED: $FAILURES issues"
    exit 1
else
    echo "HEALTH CHECK PASSED"
    exit 0
fi
EOF
    chmod +x /opt/tars/health_check.sh
    
    # Add to crontab
    (crontab -l 2>/dev/null; echo "*/5 * * * * /opt/tars/health_check.sh >> /var/log/tars/health_check.log 2>&1") | crontab -
    
    log "Monitoring configured"
}

verify_deployment() {
    log "Verifying deployment..."
    
    # Check services
    for svc in tars-agent tars-scheduler tars-ws-hub redis-server nginx; do
        if systemctl is-active --quiet "$svc"; then
            echo -e "${GREEN}✓ $svc running${NC}"
        else
            echo -e "${RED}✗ $svc NOT running${NC}"
        fi
    done
    
    # Check API endpoints
    sleep 5
    if curl -sf http://localhost:8000/health >/dev/null; then
        echo -e "${GREEN}✓ API health endpoint responding${NC}"
    else
        echo -e "${RED}✗ API health endpoint not responding${NC}"
    fi
    
    if curl -sf http://localhost:8000/api/v1/metrics >/dev/null; then
        echo -e "${GREEN}✓ Metrics endpoint responding${NC}"
    else
        echo -e "${RED}✗ Metrics endpoint not responding${NC}"
    fi
    
    echo -e "${GREEN}Deployment verification complete${NC}"
}

main() {
    check_root
    
    echo -e "${GREEN}=== TARS Deployment Script ===${NC}"
    echo "Target: Contabo VPS (Ubuntu 22.04+)"
    echo ""
    
    read -p "Continue with deployment? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
    
    install_system_packages
    create_tars_user
    setup_directories
    clone_repo
    install_python
    setup_env_file
    setup_systemd_services
    setup_nginx
    setup_firewall
    setup_logrotate
    setup_monitoring
    verify_deployment
    
    echo -e "${GREEN}=== Deployment Complete ===${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Edit /etc/tars/agent.env with your credentials"
    echo "2. Run: systemctl start tars-agent tars-scheduler tars-ws-hub"
    echo "3. Check logs: journalctl -u tars-agent -f"
    echo "4. Run health check: /opt/tars/health_check.sh"
    echo ""
    echo "IMPORTANT: Before going live:"
    echo "  - Fund the agent wallet"
    echo "  - Verify OKX API keys have correct permissions"
    echo "  - Test deposit -> trade -> withdraw cycle"
    echo "  - Set up monitoring alerts"
}

main "$@"