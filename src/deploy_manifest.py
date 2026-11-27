"""Deterministic deployment artifact manifest and safety checks."""
from __future__ import annotations

import hashlib
from pathlib import Path


REQUIRED_FILES = ("pyproject.toml", "requirements.txt", "src/runtime.py",
                  "src/strategy_lifecycle.py", "scripts/safety_freeze_check.py")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: str | Path) -> dict:
    base = Path(root)
    files = {}
    missing = []
    for name in REQUIRED_FILES:
        path = base / name
        if not path.is_file():
            missing.append(name)
        else:
            files[name] = sha256_file(path)
    deployment_issues = []
    deploy_dir = base / "deploy"
    if deploy_dir.is_dir():
        for service in deploy_dir.glob("*.service"):
            text = service.read_text(encoding="utf-8")
            if "Type=oneshot" in text:
                continue
            if "Environment=ALLOW_LIVE=false" not in text:
                deployment_issues.append(f"{service.name}:missing_allow_live_false")
            if "Environment=DRY_RUN=true" not in text:
                deployment_issues.append(f"{service.name}:missing_dry_run_true")
        for timer in deploy_dir.glob("*.timer"):
            text = timer.read_text(encoding="utf-8")
            if "OnCalendar=2026-" in text:
                deployment_issues.append(f"{timer.name}:expired_calendar")
    return {
        "artifact_type": "tarstrade_deployment_manifest",
        "required_files": list(REQUIRED_FILES),
        "files": files,
        "missing": missing,
        "deployment_issues": deployment_issues,
        "safety": {"dry_run": True, "allow_live": False},
        "ready": not missing and not deployment_issues,
    }
