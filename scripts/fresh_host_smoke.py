"""Offline fresh-host smoke check for a Tarstrade artifact."""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.deploy_manifest import build_manifest

IMPORTS = (
    "src.runtime", "src.strategy_lifecycle", "src.experiment_schema",
    "src.execution", "src.runtime_health", "src.task_tray",
)


def run_smoke(root: Path) -> dict:
    manifest = build_manifest(root)
    imports: dict[str, str] = {}
    for module in IMPORTS:
        try:
            importlib.import_module(module)
            imports[module] = "ok"
        except Exception as exc:  # noqa: BLE001 - report exact smoke failure
            imports[module] = f"error:{type(exc).__name__}"
    freeze = subprocess.run(
        [sys.executable, str(root / "scripts" / "safety_freeze_check.py")],
        cwd=root, capture_output=True, text=True, check=False,
    )
    return {
        "artifact_ready": manifest["ready"],
        "imports": imports,
        "safety_freeze": freeze.returncode == 0,
        "network_used": False,
        "live_started": False,
        "passed": bool(manifest["ready"] and all(v == "ok" for v in imports.values())
                      and freeze.returncode == 0),
    }


def main(argv=None) -> int:
    root = Path((argv or sys.argv[1:])[0]) if (argv or sys.argv[1:]) else Path(__file__).resolve().parent.parent
    result = run_smoke(root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
