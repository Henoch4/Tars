"""Print the repository-wide Tarstrade roadmap status."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.roadmap_status import status_dict


def main() -> int:
    print(json.dumps(status_dict(test_count=1682, safety_freeze=True,
                                 deployment_ready=True), indent=2,
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
