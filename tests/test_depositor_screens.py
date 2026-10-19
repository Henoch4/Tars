"""5.4 verification: each depositor screen independently functional.

Six deep-link routes serve the page with its matching section; unknown
screens 404; the manifest doctor stays green (screen routes are UI,
include_in_schema=False, same convention as /depositor).
"""
import pytest


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import src.main as main
    return TestClient(main.app)


SCREENS = {
    "hero": 'id="wallet-label"',
    "stats": 'id="stat-tvl"',
    "deposit": 'id="tab-deposit"',
    "withdraw": 'id="tab-withdraw"',
    "my-position": 'id="tab-position"',
    "verify": 'id="verify"',
    "activity": 'id="tab-activity"',
    "safety": 'id="tab-safety"',
}


@pytest.mark.parametrize("screen,marker", sorted(SCREENS.items()))
def test_each_screen_serves_its_section(client, screen, marker):
    r = client.get(f"/depositor/{screen}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert marker in r.text
    # deep-link hook + real contract calls, not a placeholder
    assert "deepLinkScreen" in r.text
    assert "vaultContract.deposit" in r.text


def test_unknown_screen_404s(client):
    assert client.get("/depositor/bogus").status_code == 404


def test_depositor_root_still_serves(client):
    r = client.get("/depositor")
    assert r.status_code == 200
    assert 'id="tab-deposit"' in r.text


def test_manifest_doctor_unaffected(client):
    from scripts.check_manifest import check_manifest
    assert check_manifest() == []
