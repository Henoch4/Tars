"""Fail-closed Phase-0 safety freeze audit for configs and deploy artifacts."""
from __future__ import annotations
import re, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TARGETS=[ROOT/'.github',ROOT/'config',ROOT/'deploy']
PATTERNS=(re.compile(r'^\s*(?:Environment\s+)?ALLOW_LIVE\s*[:=]\s*["\']?true',re.I),
          re.compile(r'^\s*(?:Environment\s+)?DRY_RUN\s*[:=]\s*["\']?false',re.I),
          re.compile(r'^\s*live_enabled\s*:\s*true',re.I))

def main() -> int:
    violations=[]
    for root in TARGETS:
        if not root.exists(): continue
        for p in root.rglob('*'):
            if not p.is_file(): continue
            try: lines=p.read_text(encoding='utf-8').splitlines()
            except UnicodeDecodeError: continue
            for n,line in enumerate(lines,1):
                if any(rx.search(line) for rx in PATTERNS): violations.append(f'{p.relative_to(ROOT)}:{n}')
    if violations:
        print('SAFETY_FREEZE_FAIL'); print('\n'.join(violations)); return 1
    print('SAFETY_FREEZE_PASS'); print('configs/deploy/CI contain no live-enabling flags')
    return 0
if __name__=='__main__': raise SystemExit(main())
