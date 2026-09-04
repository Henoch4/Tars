#!/usr/bin/env bash
# Key Rotation Script for TARS
# Rotates AGENT_WALLET_PRIVATE_KEY and OKX API credentials
# Usage: ./rotate_keys.sh [--dry-run] [--env-file /path/to/.env]

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

DRY_RUN=false
ENV_FILE=".env"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--dry-run] [--env-file /path/to/.env]"
            exit 1
            ;;
    esac
done

echo -e "${GREEN}=== TARS Key Rotation Script ===${NC}"
echo "Environment file: $ENV_FILE"
echo "Dry run: $DRY_RUN"
echo ""

# Check if env file exists
if [[ ! -f "$ENV_FILE" ]]; then
    echo -e "${RED}Error: Environment file not found: $ENV_FILE${NC}"
    exit 1
fi

# Load current env
source "$ENV_FILE"

# Function to generate a new Ethereum private key
generate_eth_key() {
    python3 -c "
import secrets
key = '0x' + secrets.token_hex(32)
print(key)
"
}

# Function to generate a new OKX API key (placeholder - requires OKX API)
generate_okx_credentials() {
    echo "OKX API keys must be generated via OKX dashboard."
    echo "Please visit: https://www.okx.com/account/api"
    echo "Create new API key with: Read + Trade permissions, NO withdrawal"
    echo ""
    read -p "Enter new OKX API Key: " NEW_OKX_API_KEY
    read -p "Enter new OKX Secret Key: " NEW_OKX_SECRET_KEY
    read -p "Enter new OKX Passphrase: " NEW_OKX_PASSPHRASE
    
    if [[ -z "$NEW_OKX_API_KEY" || -z "$NEW_OKX_SECRET_KEY" || -z "$NEW_OKX_PASSPHRASE" ]]; then
        echo "Error: All OKX credentials required"
        exit 1
    fi
    
    echo "OKX_API_KEY=$NEW_OKX_API_KEY"
    echo "OKX_SECRET_KEY=$NEW_OKX_SECRET_KEY"
    echo "OKX_PASSPHRASE=$NEW_OKX_PASSPHRASE"
}

# Backup current .env
BACKUP_FILE="${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
if [[ "$DRY_RUN" == "false" ]]; then
    cp "$ENV_FILE" "$BACKUP_FILE"
    echo -e "${GREEN}Backed up current .env to $BACKUP_FILE${NC}"
fi

# Generate new Ethereum private key for agent wallet
echo -e "${YELLOW}Generating new agent wallet private key...${NC}"
NEW_AGENT_KEY=$(generate_eth_key)
NEW_AGENT_ADDRESS=$(python3 -c "
from eth_account import Account
acct = Account.from_key('$NEW_AGENT_KEY')
print(acct.address)
")

echo -e "${GREEN}New Agent Wallet:${NC}"
echo "  Address: $NEW_AGENT_ADDRESS"
echo "  Private Key: $NEW_AGENT_KEY"
echo ""

# Get new OKX credentials
echo -e "${YELLOW}Generating new OKX API credentials...${NC}"
OKX_CREDS=$(generate_okx_credentials)
eval "$OKX_CREDS"

# Prepare new .env content
NEW_ENV_CONTENT="# TARS Environment Configuration
# Generated: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# ROTATED: $(date -u +"%Y-%m-%d %H:%M:%S UTC")

# Trading Mode
DRY_RUN=false
ALLOW_LIVE=true

# Agent Identity
AGENT_WALLET_PRIVATE_KEY=$NEW_AGENT_KEY
AGENT_API_TOKEN=$(openssl rand -hex 32)

# OKX Credentials (ROTATED)
OKX_API_KEY=$NEW_OKX_API_KEY
OKX_SECRET_KEY=$NEW_OKX_SECRET_KEY
OKX_PASSPHRASE=$NEW_OKX_PASSPHRASE

# OKX Configuration
OKX_BASE_URL=https://www.okx.com
OKX_DEMO=false

# X Layer Configuration
XLAYER_RPC_URL=https://rpc.xlayer.tech
XLAYER_RPC_URL_FALLBACK=https://rpc.xlayer.tech
XLAYER_CHAIN_ID=1952
AUDIT_CONTRACT_ADDRESS=0x...

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
"

# Write new .env
if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "${YELLOW}[DRY RUN] Would write new .env with:${NC}"
    echo "$NEW_ENV_CONTENT"
else
    echo "$NEW_ENV_CONTENT" > "$ENV_FILE"
    echo -e "${GREEN}Updated $ENV_FILE with rotated keys${NC}"
fi

# Output summary
echo ""
echo -e "${GREEN}=== Key Rotation Complete ===${NC}"
echo ""
echo "New Agent Wallet:"
echo "  Address: $NEW_AGENT_ADDRESS"
echo "  Private Key: $NEW_AGENT_KEY"
echo ""
echo "New OKX Credentials:"
echo "  API Key: $NEW_OKX_API_KEY"
echo "  Secret Key: $NEW_OKX_SECRET_KEY"
echo "  Passphrase: $NEW_OKX_PASSPHRASE"
echo ""
echo "Backup saved to: $BACKUP_FILE"
echo ""
echo -e "${YELLOW}IMPORTANT NEXT STEPS:${NC}"
echo "1. Fund the new agent wallet: $NEW_AGENT_ADDRESS"
echo "2. Update OKX API key permissions (Read + Trade, NO withdrawal)"
echo "3. Update contract risk params with new agent address"
echo "3. Test deposit -> trade -> withdraw cycle"
echo "4. Update monitoring/alerting with new agent address"
echo ""
echo -e "${GREEN}Key rotation complete!${NC}"