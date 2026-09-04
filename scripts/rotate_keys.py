#!/usr/bin/env python3
"""
Key Rotation Script for TARS
Rotates AGENT_WALLET_PRIVATE_KEY and exchange API credentials (OKX/Binance)
Usage: python scripts/rotate_keys.py [--dry-run] [--env-file /path/to/.env]
"""

import argparse
import os
import sys
import secrets
import json
import shutil
from datetime import datetime
from pathlib import Path
from eth_account import Account
import subprocess

try:
    import web3
    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False

# Colors
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
NC = '\033[0m'


def generate_eth_key() -> str:
    """Generate a new Ethereum private key."""
    return '0x' + secrets.token_hex(32)


def generate_eth_address(private_key: str) -> str:
    """Get Ethereum address from private key."""
    acct = Account.from_key(private_key)
    return acct.address


def get_exchange_credentials(exchange: str) -> dict:
    """Prompt user for exchange credentials based on exchange type."""
    if exchange.lower() == 'okx':
        print("OKX API keys must be generated via OKX dashboard.")
        print("Please visit: https://www.okx.com/account/api")
        print("Create new API key with: Read + Trade permissions, NO withdrawal")
        print()
        
        api_key = input("Enter new OKX API Key: ").strip()
        secret_key = input("Enter new OKX Secret Key: ").strip()
        passphrase = input("Enter new OKX Passphrase: ").strip()
        
        if not api_key or not secret_key or not passphrase:
            raise ValueError("All OKX credentials required")
        
        return {
            'api_key': api_key,
            'secret_key': secret_key,
            'passphrase': passphrase,
            'exchange': 'okx'
        }
    elif exchange.lower() == 'binance':
        print("Binance API keys must be generated via Binance dashboard.")
        print("Please visit: https://www.binance.com/en/my/settings/api-management")
        print("Create new API key with: Read + Trade permissions, NO withdrawal")
        print()
        
        api_key = input("Enter new Binance API Key: ").strip()
        secret_key = input("Enter new Binance Secret Key: ").strip()
        
        if not api_key or not secret_key:
            raise ValueError("Both Binance API Key and Secret Key required")
        
        return {
            'api_key': api_key,
            'secret_key': secret_key,
            'passphrase': '',  # Binance doesn't use passphrase
            'exchange': 'binance'
        }
    else:
        raise ValueError(f"Unsupported exchange: {exchange}")


def load_env_file(env_path: Path) -> dict:
    """Load environment variables from .env file."""
    env = {}
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env[key.strip()] = value.strip()
    return env


def write_env_file(env_path: Path, env: dict, dry_run: bool = False):
    """Write environment variables to .env file."""
    lines = []
    for key, value in env.items():
        lines.append(f"{key}={value}")
    
    content = '\n'.join(lines) + '\n'
    
    if not dry_run:
        # Backup existing .env
        backup_path = env_path.with_suffix(f'.env.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}')
        shutil.copy2(env_path, backup_path)
        print(f"Backed up current .env to {backup_path}")
        
        with open(env_path, 'w') as f:
            f.write(content)
        print(f"Updated {env_path}")
    else:
        print("[DRY RUN] Would write:")
        print("---")
        print('\n'.join(lines[:20]) + "\n... (truncated)")
        print("---")


def generate_new_keys():
    """Generate new Ethereum private key and address."""
    private_key = '0x' + secrets.token_hex(32)
    acct = Account.from_key(private_key)
    return private_key, acct.address


def get_okx_credentials() -> tuple:
    """Get OKX credentials from user input."""
    print("OKX API keys must be generated via OKX dashboard.")
    print("Please visit: https://www.okx.com/account/api")
    print("Create new API key with: Read + Trade permissions, NO withdrawal")
    print()
    
    api_key = input("Enter new OKX API Key: ").strip()
    secret_key = input("Enter new OKX Secret Key: ").strip()
    passphrase = input("Enter new OKX Passphrase: ").strip()
    
    if not api_key or not secret_key or not passphrase:
        raise ValueError("All OKX credentials required")
    
    return api_key, secret_key, passphrase


def main():
    parser = argparse.ArgumentParser(description='Rotate TARS keys')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    parser.add_argument('--env-file', type=str, default='.env', help='Path to .env file')
    args = parser.parse_args()
    
    env_path = Path(args.env_file).resolve()
    
    print(f"{GREEN}=== TARS Key Rotation Script ==={NC}")
    print(f"Environment file: {env_path}")
    print(f"Dry run: {args.dry_run}")
    print()
    
    # Load current env
    current_env = load_env_file(Path(args.env_file))
    
    # Read exchange from env
    exchange = current_env.get('EXCHANGE', 'okx').lower()
    print(f"Exchange: {exchange.upper()}")
    print()
    
    if args.dry_run:
        print(f"{YELLOW}[DRY RUN] Would rotate keys. No changes will be made.{NC}")
        print()
    
    # Generate new Ethereum key
    print(f"{YELLOW}Generating new Ethereum private key...{NC}")
    new_private_key, new_address = generate_new_keys()
    print(f"New Agent Wallet:")
    print(f"  Address: {new_address}")
    print(f"  Private Key: {new_private_key}")
    print()
    
    # Get exchange credentials (skip in dry-run)
    if args.dry_run:
        if exchange == 'okx':
            new_exchange_creds = {
                'api_key': "OKX_API_KEY_PLACEHOLDER",
                'secret_key': "OKX_SECRET_KEY_PLACEHOLDER",
                'passphrase': "OKX_PASSPHRASE_PLACEHOLDER",
                'exchange': 'okx'
            }
        elif exchange == 'binance':
            new_exchange_creds = {
                'api_key': "BINANCE_API_KEY_PLACEHOLDER",
                'secret_key': "BINANCE_SECRET_KEY_PLACEHOLDER",
                'passphrase': '',
                'exchange': 'binance'
            }
        else:
            new_exchange_creds = {}
    else:
        new_exchange_creds = get_exchange_credentials(exchange)
    
    # Build new env - preserve all existing keys, update rotated ones
    new_env = dict(current_env)  # Preserve all existing keys
    
    # Update rotated keys
    new_env.update({
        'DRY_RUN': 'false',
        'ALLOW_LIVE': 'true',
        'AGENT_WALLET_PRIVATE_KEY': new_private_key,
        'AGENT_API_TOKEN': secrets.token_hex(32),
    })
    
    # Update exchange-specific credentials
    if exchange.lower() == 'okx':
        new_env.update({
            'OKX_API_KEY': new_exchange_creds['api_key'],
            'OKX_SECRET_KEY': new_exchange_creds['secret_key'],
            'OKX_PASSPHRASE': new_exchange_creds['passphrase'],
            'OKX_BASE_URL': 'https://www.okx.com',
            'OKX_DEMO': 'false',
        })
    elif exchange.lower() == 'binance':
        new_env.update({
            'BINANCE_API_KEY': new_exchange_creds['api_key'],
            'BINANCE_API_SECRET': new_exchange_creds['secret_key'],
            'BINANCE_TESTNET': 'false',
        })
    
    # Preserve other critical settings (don't hardcode, preserve from current_env)
    preserved_keys = [
        'MAX_POSITION_USD', 'MAX_DAILY_LOSS_USD', 'MAX_DAILY_TRADES',
        'MAX_DAILY_VOLUME_USD', 'MAX_LEVERAGE', 'MIN_CONFIDENCE_BPS',
        'MAX_PRICE_AGE_SECONDS', 'LOSS_COOLDOWN_MINUTES',
        'DRAWDOWN_WINDOW_DAYS', 'DRAWDOWN_LOSS_MULT',
        'VAULT_MIN_DEPOSIT', 'VAULT_MAX_TVL',
        'XLAYER_RPC_URL', 'XLAYER_RPC_URL_FALLBACK', 'XLAYER_CHAIN_ID',
        'AUDIT_CONTRACT_ADDRESS', 'VAULT_CONTRACT_ADDRESS',
        'LOG_LEVEL', 'AGENT_API_TOKEN', 'AGENT_ID',
        'EXCHANGE', 'OKX_BASE_URL', 'OKX_DEMO',
        'XLAYER_RPC_URL', 'XLAYER_RPC_URL_FALLBACK',
        'XLAYER_CHAIN_ID', 'AUDIT_CONTRACT_ADDRESS',
        'VAULT_CONTRACT_ADDRESS', 'PAY_TO_ADDRESS',
        'MERMAIL_API_KEY', 'MERMAIL_API_URL',
    ]
    
    for key in preserved_keys:
        if key in current_env:
            new_env[key] = current_env[key]
    
    # Ensure required keys have defaults
    defaults = {
        'DRY_RUN': 'false',
        'ALLOW_LIVE': 'true',
        'AGENT_API_TOKEN': secrets.token_hex(32),
        'XLAYER_RPC_URL': 'https://rpc.xlayer.tech',
        'XLAYER_RPC_URL_FALLBACK': 'https://rpc.xlayer.tech',
        'XLAYER_CHAIN_ID': '1952',
        'AUDIT_CONTRACT_ADDRESS': '0x...',
        'VAULT_CONTRACT_ADDRESS': '0x...',
        'MAX_POSITION_USD': '5000',
        'MAX_DAILY_LOSS_USD': '500',
        'MAX_DAILY_TRADES': '10',
        'MAX_DAILY_VOLUME_USD': '50000',
        'MAX_LEVERAGE': '5.0',
        'MIN_CONFIDENCE_BPS': '7000',
        'MAX_PRICE_AGE_SECONDS': '60',
        'LOSS_COOLDOWN_MINUTES': '30',
        'DRAWDOWN_WINDOW_DAYS': '3',
        'DRAWDOWN_LOSS_MULT': '2.0',
        'VAULT_MIN_DEPOSIT': '10',
        'VAULT_MAX_TVL': '100000',
        'XLAYER_RPC_URL': 'https://rpc.xlayer.tech',
        'XLAYER_RPC_URL_FALLBACK': 'https://rpc.xlayer.tech',
        'XLAYER_CHAIN_ID': '1952',
        'AUDIT_CONTRACT_ADDRESS': '0x...',
        'VAULT_CONTRACT_ADDRESS': '0x...',
        'LOG_LEVEL': 'INFO',
    }
    
    for key, default in defaults.items():
        if key not in new_env:
            new_env[key] = default
    
    if not args.dry_run:
        # Backup current .env
        backup_path = Path('.env.backup.' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        shutil.copy2('.env', backup_path)
        print(f"Backed up current .env to {backup_path}")
        
        # Write new .env
        write_env_file(Path('.env'), new_env)
        
        print(f"\n{GREEN}=== Key Rotation Complete ==={NC}")
        print()
        print("New Agent Wallet:")
        print(f"  Address: {Account.from_key(new_private_key).address}")
        print(f"  Private Key: {new_private_key}")
        print()
        
        if exchange.lower() == 'okx':
            print("New OKX Credentials:")
            print(f"  API Key: ***")
            print(f"  Secret Key: ***")
            print(f"  Passphrase: ***")
        elif exchange.lower() == 'binance':
            print("New Binance Credentials:")
            print(f"  API Key: ***")
            print(f"  Secret Key: ***")
        print()
        print(f"Backup saved to: (would be created)")
        print()
        print("IMPORTANT NEXT STEPS:")
        print("1. Fund the new agent wallet")
        print("2. Update exchange API key permissions (Read + Trade, NO withdrawal)")
        print("3. Update contract risk params with new agent address")
        print("4. Test deposit -> trade -> withdraw cycle")
        print("4. Update monitoring/alerting with new agent address")
    else:
        print(f"\n{YELLOW}[DRY RUN] Would rotate keys. No changes made.{NC}")
        print()
        print("New keys would be generated:")
        print(f"  New Agent Address: {Account.from_key('0x' + secrets.token_hex(32)).address}")
        if exchange.lower() == 'okx':
            print(f"  New OKX API Key: ***")
            print(f"  New OKX Secret: ***")
            print(f"  New OKX Passphrase: ***")
        elif exchange.lower() == 'binance':
            print(f"  New Binance API Key: ***")
            print(f"  New Binance Secret: ***")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())