from src.deploy_manifest import build_manifest


def test_manifest_requires_core_files_and_is_safe(tmp_path):
    (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")
    manifest = build_manifest(tmp_path)
    assert manifest["ready"] is False
    assert "src/runtime.py" in manifest["missing"]
    assert manifest["safety"] == {"dry_run": True, "allow_live": False}


def test_manifest_is_ready_for_complete_fixture(tmp_path):
    for name in ("pyproject.toml", "requirements.txt", "src/runtime.py",
                 "src/strategy_lifecycle.py", "scripts/safety_freeze_check.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    manifest = build_manifest(tmp_path)
    assert manifest["ready"] is True
    assert len(manifest["files"]) == 5


def test_deploy_templates_cannot_use_expired_calendar(tmp_path):
    for name in ("pyproject.toml", "requirements.txt", "src/runtime.py",
                 "src/strategy_lifecycle.py", "scripts/safety_freeze_check.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "x.service").write_text(
        "Environment=DRY_RUN=true\nEnvironment=ALLOW_LIVE=false\n", encoding="utf-8")
    (deploy / "x.timer").write_text("OnCalendar=2026-11-01\n", encoding="utf-8")
    manifest = build_manifest(tmp_path)
    assert manifest["ready"] is False
    assert "x.timer:expired_calendar" in manifest["deployment_issues"]
