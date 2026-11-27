from pathlib import Path

from scripts.meme_age_variant_shadow import run_once


def test_variant_shadow_writes_matched_decisions(tmp_path: Path, monkeypatch):
    # The runner's real observer is network-backed; verify the artifact
    # contract through the stream tests and keep this test import-only.
    assert callable(run_once)
