from scripts.goplus_calibration import main


def test_calibration_refuses_without_secret(monkeypatch, capsys):
    monkeypatch.delenv("GOPLUS_SOLANA_TOKEN", raising=False)
    assert main(["MINT1", "MINT2"]) == 2
    assert "missing" in capsys.readouterr().out.lower()
