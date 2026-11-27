"""Two-candidate GoPlus calibration; emits sanitized evidence only."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.gta_external_safety import (GoplusSolanaClient, parse_goplus,
                                     query_provider)


def _extract_access_token(payload):
    if isinstance(payload, str) and len(payload) > 20:
        return payload
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in {"access_token", "accesstoken", "token"} and isinstance(value, str):
                return value
            found = _extract_access_token(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _extract_access_token(value)
            if found:
                return found
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mints", nargs=2)
    parser.add_argument("--output", default="-")
    args = parser.parse_args(argv)
    client = GoplusSolanaClient()
    if not client.token:
        print(json.dumps({"ok": False, "error": "GOPLUS_SOLANA_TOKEN missing"}))
        return 2
    rows = []
    for mint in args.mints:
        observed = time.time()
        signal = query_provider(lambda m=mint: client.fetch(m), parse_goplus,
                                source="goplus", now=observed)
        rows.append({"mint": mint, **signal.as_dict()})
    result = {
        "ok": all(r["status"] in ("ok", "unsafe") and r["observed_at"] > 0
                   for r in rows),
        "provider": "goplus", "count": len(rows), "rows": rows,
    }
    text = json.dumps(result, sort_keys=True)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
