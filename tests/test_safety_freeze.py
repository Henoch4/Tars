from pathlib import Path
from scripts.safety_freeze_check import PATTERNS

def test_live_enabling_patterns_are_detected():
    assert any(rx.search('ALLOW_LIVE=true') for rx in PATTERNS)
    assert any(rx.search('DRY_RUN=false') for rx in PATTERNS)
    assert any(rx.search('live_enabled: true') for rx in PATTERNS)

def test_deploy_and_ci_have_no_live_enabling_flags():
    root=Path(__file__).resolve().parents[1]
    for base in (root/'.github', root/'config', root/'deploy'):
        for p in base.rglob('*'):
            if p.is_file():
                text=p.read_text(encoding='utf-8', errors='ignore')
                assert not any(rx.search(line) for line in text.splitlines() for rx in PATTERNS), p
