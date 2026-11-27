"""Small operator CLI for the append-only Tarstrade task tray."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import sys

# Running `python scripts/task_tray.py` sets sys.path to scripts/, not the
# repository root. Keep the operator command usable from a fresh checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.task_tray import TaskTray

def main():
 p=argparse.ArgumentParser(); p.add_argument('command',choices=['list','ready']); p.add_argument('--path',default='data/task_tray.jsonl'); a=p.parse_args(); tray=TaskTray(Path(a.path)); tasks=tray.latest(); rows=tray.ready() if a.command=='ready' else list(tasks.values()); print(json.dumps([t.__dict__ for t in rows],sort_keys=True,default=list))
if __name__=='__main__': main()
