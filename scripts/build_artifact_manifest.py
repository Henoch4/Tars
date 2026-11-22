"""Build a sanitized deployment manifest without deploying anything."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.deploy_manifest import build_manifest


def main(argv=None) -> int:
    args = argv or sys.argv[1:]
    root = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    manifest = build_manifest(root)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
