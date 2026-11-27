import json
import subprocess
import sys


def test_roadmap_status_cli_emits_all_phases():
    result = subprocess.run(
        [sys.executable, "scripts/roadmap_status.py"],
        capture_output=True, text=True, check=True,
    )
    phases = {row["phase"]: row for row in json.loads(result.stdout)}
    assert "A" in phases and "F" in phases and "GTA" in phases
    assert phases["GTA"]["status"] == "gated"
