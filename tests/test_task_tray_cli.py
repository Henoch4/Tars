import json
import subprocess
import sys


def test_task_tray_cli_runs_from_scripts_path(tmp_path):
    path = tmp_path / "tray.jsonl"
    result = subprocess.run(
        [sys.executable, "scripts/task_tray.py", "list", "--path", str(path)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == []
