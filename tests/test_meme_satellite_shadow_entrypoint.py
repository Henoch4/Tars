from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shadow_entrypoint_and_unit_are_inert():
    script = (ROOT / "scripts" / "meme_satellite_shadow.py").read_text(encoding="utf-8")
    unit = (ROOT / "deploy" / "tars-meme-supervisor-shadow.service").read_text(encoding="utf-8")
    assert "live_enabled=False" in script
    assert "balance_reader=None" in script
    assert "Environment=DRY_RUN=true" in unit
    assert "Environment=ALLOW_LIVE=false" in unit
    assert "meme_satellite_shadow.py --loop" in unit


def test_shadow_window_timers_are_readiness_gated():
    start = (ROOT / "deploy" / "tars-meme-supervisor-shadow.timer").read_text(encoding="utf-8")
    stop = (ROOT / "deploy" / "tars-meme-supervisor-shadow-stop.timer").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "tars-meme-supervisor-shadow.service").read_text(encoding="utf-8")
    assert "OnBootSec=10min" in start
    assert "OnBootSec=365d" in stop
    assert "ConditionPathExists=/etc/tars/ENABLE_MEME_SUPERVISOR_SHADOW" in service
