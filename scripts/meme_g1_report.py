"""G1 progress report — read-only. Never trades, never writes.

G1 (the one build gate): >=7 days of paper operation AND >=30 net-positive
paper round-trips. This prints progress against it from the blotter +
positions file. Safe to run at any time, including mid-G1 window.

Usage:
    ./venv/bin/python scripts/meme_g1_report.py
    ./venv/bin/python scripts/meme_g1_report.py --json
    ./venv/bin/python scripts/meme_g1_report.py --blotter PATH --positions PATH
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_DATA_DIR = "/opt/tars/data/meme_satellite"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Report progress toward G1.")
    ap.add_argument("--blotter", default=os.path.join(DEFAULT_DATA_DIR, "blotter.jsonl"),
                    help="path to blotter.jsonl")
    ap.add_argument("--positions", default=os.path.join(DEFAULT_DATA_DIR, "positions.json"),
                    help="path to positions.json (optional enrichment)")
    ap.add_argument("--json", action="store_true", help="emit the raw report dict")
    args = ap.parse_args(argv)

    from src.meme_g1 import build_g1_report, render_g1_report

    rep = build_g1_report(args.blotter, args.positions)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print(render_g1_report(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
