"""Read-only Token-2022 mint calibration.

Loads RPC credentials from the local .env (without printing them), fetches
mint accounts at finalized commitment, and emits sanitized inspector rows.
No signing, wallet access, or broadcast path exists in this script.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.gta_mint_calibration import inspect_account  # noqa: E402


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _rpc_url() -> str:
    url = os.getenv("HELIUS_RPC_URL", "").strip() or os.getenv("SOLANA_RPC_URL", "").strip()
    if url:
        return url
    key = os.getenv("HELIUS_API_KEY", "").strip()
    if key:
        return f"https://mainnet.helius-rpc.com/?api-key={key}"
    raise RuntimeError("No Helius or Solana RPC configuration found")


def fetch(url: str, mint: str) -> dict:
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
        "params": [mint, {"encoding": "base64", "commitment": "finalized"}],
    }).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mints", nargs="+", help="Solana mint addresses")
    parser.add_argument("--env-file", default=None,
                        help="credential env file (defaults to /etc/tars/agent.env, then .env)")
    parser.add_argument("--max-age", type=int, default=900)
    args = parser.parse_args()
    env_path = Path(args.env_file) if args.env_file else Path("/etc/tars/agent.env")
    if not env_path.exists() and args.env_file is None:
        env_path = Path(".env")
    _load_dotenv(env_path)
    url = _rpc_url()
    rows = []
    for mint in args.mints:
        observed = time.time()
        try:
            response = fetch(url, mint)
            rows.append(inspect_account(response, "finalized", observed_at=observed,
                                        mint=mint, now=observed, max_age_s=args.max_age))
        except urllib.error.HTTPError as exc:
            rows.append({"mint": mint, "classification": "unknown",
                         "error_code": "rpc_http_error", "http_status": exc.code,
                         "observed_at": observed, "commitment": "finalized"})
        except urllib.error.URLError as exc:
            rows.append({"mint": mint, "classification": "unknown",
                         "error_code": "rpc_transport_error",
                         "reason": type(exc.reason).__name__,
                         "observed_at": observed, "commitment": "finalized"})
        except Exception as exc:  # sanitized: never include URL or credential data
            rows.append({"mint": mint, "classification": "unknown",
                         "error_code": "rpc_error", "reason": type(exc).__name__,
                         "observed_at": observed, "commitment": "finalized"})
    print(json.dumps({"ok": True, "observations": rows}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
