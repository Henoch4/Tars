"""Two-mint, read-only external safety calibration."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.gta_external_safety import (GoplusSolanaClient, RugcheckSafetyClient,
                                     combine_external_signals, parse_goplus,
                                     parse_rugcheck, query_provider)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mints", nargs="+")
    ap.add_argument("--env-file", default="/etc/tars/agent.env")
    args = ap.parse_args()
    env = Path(args.env_file)
    if not env.exists():
        env = Path(".env")
    load_env(env)
    go = GoplusSolanaClient()
    rug = RugcheckSafetyClient()
    rows = []
    for mint in args.mints:
        g = query_provider(lambda: go.fetch(mint), parse_goplus, source="goplus")
        r = query_provider(lambda: rug.fetch(mint), parse_rugcheck, source="rugcheck")
        combined = combine_external_signals(g, r)
        rows.append({"mint": mint, "providers": [g.as_dict(), r.as_dict()],
                     "combined": combined.as_dict(), "observed_at": time.time()})
    print(json.dumps({"ok": True, "observations": rows}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
