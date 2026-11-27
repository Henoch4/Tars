from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shadow_unit_is_explicitly_paper_only():
    unit = (ROOT / "deploy" / "tars-gta-shadow.service").read_text(encoding="utf-8")
    assert "User=tars" in unit
    assert "Environment=DRY_RUN=true" in unit
    assert "Environment=ALLOW_LIVE=false" in unit
    assert "gta_shadow.py --loop" in unit
    assert "gta_live_executor" not in unit
    assert "meme_execute" not in unit


def test_runbook_excludes_live_modules():
    runbook = (ROOT / "docs" / "gta_shadow_deployment_runbook.md").read_text(encoding="utf-8")
    assert "gta_live_executor.py" in runbook
    assert "gta_live_runner.py" in runbook
    assert "wallet/signing module" in runbook


def test_shadow_config_keeps_live_flags_off():
    config = (ROOT / "config" / "gta_event.yaml").read_text(encoding="utf-8")
    assert "enabled: false" in config
    assert "live_enabled: false" in config


def test_shadow_stop_timer_is_readiness_gated():
    timer = (ROOT / "deploy" / "tars-gta-shadow-stop.timer").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "tars-gta-shadow-stop.service").read_text(encoding="utf-8")
    assert "OnBootSec=365d" in timer
    assert "ConditionPathExists=/etc/tars/ENABLE_GTA_SHADOW" in service
    service = (ROOT / "deploy" / "tars-gta-shadow-stop.service").read_text(encoding="utf-8")
    assert "systemctl stop tars-gta-shadow.service" in service
