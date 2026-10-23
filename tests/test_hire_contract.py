"""Public hire contract: the manifest's documented request must be accepted.

The listing went live on OKX.AI while the manifest documented ``mode: "demo"``
and a ``{"data": [...]}`` balance envelope, and the API answered 422 then 400
to exactly that body. These tests pin the three things that failed from the
outside:

- every mode the manifest names is accepted (and unknown modes still 422),
- every balance shape an agent can forward is normalized, and the normalized
  value is what the audit scores (validating without using it turned an
  envelope into an empty account),
- the manifest's own curl example is a valid request that produces a report,
  and the identity it serves is the one OKX listed.
"""
import json
import pathlib

import pytest

from src.auditor import _validate_balance_data, run_audit_from_data
from src.main import HIRE_MODE_ALIASES, HireRequest

MANIFEST = json.loads(pathlib.Path("manifest.json").read_text(encoding="utf-8"))

ROW = {"ccy": "USDT", "availBal": "9000", "cashBal": "10000",
       "eq": "10000", "upl": "0"}
TRADING = {"totalEq": "10000", "details": [ROW]}
POSITIONS = [{"instId": "BTC-USDT-SWAP", "pos": "0.1", "avgPx": "50000",
              "upl": "12.5", "lever": "3", "liqPx": "40000",
              "mgnMode": "cross"}]


def _manifest_example_body() -> dict:
    """Pull the JSON body out of the manifest's curl example."""
    example = MANIFEST["service"]["request"]["example"]
    start = example.index("-d '") + len("-d '")
    end = example.rindex("'")
    return json.loads(example[start:end])


# -- identity ----------------------------------------------------------

def test_manifest_serves_the_listed_name():
    assert MANIFEST["name"] == "TARS Autonomous Trader"
    assert MANIFEST["service"]["name"]


def test_manifest_does_not_claim_a_live_track_record():
    disclosure = MANIFEST["risk_disclosure"].lower()
    assert "paper" in disclosure
    assert "no live track record" in disclosure or "not" in disclosure
    assert MANIFEST["live_mode_enabled"] is False


# -- mode vocabulary ---------------------------------------------------

@pytest.mark.parametrize("mode", ["own_account", "demo", "live", "data",
                                  "DEMO", " own_account "])
def test_documented_modes_are_accepted(mode):
    req = HireRequest(mode=mode)
    assert req.mode == mode.strip().lower()


@pytest.mark.parametrize("mode", ["nope", "", "own-account", "paper"])
def test_unknown_modes_still_fail_closed(mode):
    with pytest.raises(Exception):
        HireRequest(mode=mode)


def test_mode_shorthand_selects_profile():
    assert HIRE_MODE_ALIASES[HireRequest(mode="demo").mode] == "demo"
    assert HIRE_MODE_ALIASES[HireRequest(mode="live").mode] == "live"
    # own_account keeps whatever profile_mode says
    assert HIRE_MODE_ALIASES[HireRequest(mode="own_account").mode] is None


# -- balance shapes ----------------------------------------------------

@pytest.mark.parametrize("balance", [
    {"trading": TRADING},                              # canonical
    {"data": [{"trading": TRADING}]},                  # balance-all envelope
    {"data": [dict(TRADING)]},                         # envelope, bare row
    {"data": {"trading": TRADING}},                    # envelope as object
    dict(TRADING),                                     # bare account row
])
def test_every_documented_balance_shape_normalizes(balance):
    assert _validate_balance_data(balance)["trading"]["totalEq"] == "10000"


def test_normalized_shape_is_what_gets_scored():
    """Envelope input must score the account, not an empty dict."""
    canonical = run_audit_from_data({"trading": TRADING}, POSITIONS)
    envelope = run_audit_from_data({"data": [{"trading": TRADING}]}, POSITIONS)
    assert envelope.risk_score == canonical.risk_score > 0
    assert [f.code for f in envelope.flags] == [f.code for f in canonical.flags]
    assert "10,000.00" in envelope.report_md
    assert "10,000.00" in canonical.report_md


def test_missing_total_eq_is_still_rejected():
    with pytest.raises(ValueError, match="totalEq is required"):
        _validate_balance_data({"data": [{"trading": {"details": []}}]})


def test_non_numeric_total_eq_is_still_rejected():
    with pytest.raises(ValueError, match="totalEq must be a numeric"):
        _validate_balance_data({"trading": {"totalEq": "lots"}})


# -- the manifest example is executable (docs can't drift) --------------

def test_manifest_example_is_a_valid_hire_request():
    body = _manifest_example_body()
    req = HireRequest(**body)          # 422 in the old code
    report = run_audit_from_data(      # 400 in the old code
        balance_data=req.balance_data,
        positions_data=req.positions_data,
        inst_type=req.inst_type,
    )
    assert report.risk_score > 0
    assert report.report_md.startswith("# Portfolio Risk Audit")


def test_manifest_example_positions_use_the_documented_field_names():
    body = _manifest_example_body()
    pos = body["positions_data"][0]
    for field in ("instId", "pos", "avgPx"):
        assert field in pos
