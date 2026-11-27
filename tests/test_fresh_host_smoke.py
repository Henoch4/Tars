from scripts.fresh_host_smoke import run_smoke


def test_fresh_host_smoke_is_offline_and_passes_repo():
    result = run_smoke(__import__("pathlib").Path.cwd())
    assert result["passed"] is True
    assert result["network_used"] is False
    assert result["live_started"] is False
    assert all(value == "ok" for value in result["imports"].values())
