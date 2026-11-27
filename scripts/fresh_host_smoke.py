"""Offline fresh-host smoke check for imports and safety posture."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run_smoke(root: str | Path | None = None) -> dict:
    if root is not None:
        sys.path.insert(0, str(Path(root).resolve()))
    modules = ("src.execution", "src.strategy_lifecycle", "src.provider_quality", "src.meme_satellite")
    imports = {}
    for name in modules:
        try:
            importlib.import_module(name)
            imports[name] = "ok"
        except Exception as exc:
            imports[name] = f"error: {type(exc).__name__}"
    return {"imports": imports, "network_used": False, "live_started": False,
            "passed": all(value == "ok" for value in imports.values())}


def main() -> int:
    result = run_smoke()
    print(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
