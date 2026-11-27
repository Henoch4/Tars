import base64
import struct

import pytest


@pytest.mark.parametrize("first,last,declared,code", [
    (18,1,108,"sell_unassessed"), (18,14,64,"hook_unassessed"),
    (60000,1,108,"unsupported_extension"), (18,14,63,"invalid_extension_header"),
    (18,14,65,"truncated_extension"),
])
def test_walk(first,last,declared,code):
    raw = bytearray(166)
    raw[45] = raw[165] = 1
    raw.extend(struct.pack("<HH", first,64) + bytes(64))
    raw.extend(struct.pack("<HH",last,declared) + bytes([1])*min(declared,64)
               if last == 14 else struct.pack("<HH",last,108)+bytes(108))
    value = dict(owner=TOKEN_2022_PROGRAM_ID, executable=False,
                 data=[base64.b64encode(raw).decode(),"base64"])
    result = inspect_account({"result":{"context":{"slot":1},"value":value}}, "finalized")
    assert result["error_code"] == code
    assert [e["offset"] for e in result["extension_walk"]] == [166,234]
    if last == 1:
        assert "fee_config" in result["extension_walk"][1]
    assert result["sell_restrictions"] == "unknown"

from src.gta_mint_calibration import inspect_account, TOKEN_2022_PROGRAM_ID


@pytest.mark.parametrize("suffix,trailing,fee,code", [
    (b"", [], "absent", "sell_unassessed"),
    (b"", [(1,bytes(108))], "configured", "sell_unassessed"),
    (b"", [(14,bytes([1])*64)], "absent", "hook_unassessed"),
    (b"x", [(1,bytes(108))], "unknown", "invalid_token_metadata"),
    (b"", [(1,bytes(108)),(60000,b"x")], "unknown", "unsupported_extension"),
])
def test_metadata_boundary(suffix,trailing,fee,code):
    metadata = bytes(64) + struct.pack("<I", 3) + b"GTA" + bytes(12) + suffix
    raw = bytearray(166)
    raw[45] = raw[165] = 1
    for kind,data in [(19,metadata), *trailing]:
        raw.extend(struct.pack("<HH",kind,len(data)) + data)
    account = dict(owner=TOKEN_2022_PROGRAM_ID, executable=False,
                   data=[base64.b64encode(raw).decode(),"base64"])
    row = inspect_account({"result":{"context":{"slot":7},"value":account}}, "finalized")
    assert (row["error_code"],row["transfer_fee"],row["sell_restrictions"]) == (code,fee,"unknown")
    assert row["extension_walk"][0]["status"] == ("malformed" if suffix else "decoded")


@pytest.mark.parametrize("ext,fee,hook", [
    (b"", "absent", None),
    (struct.pack("<HH", 1, 108) + bytes(90) + struct.pack("<QQH", 12, 500, 25), "configured", None),
    (struct.pack("<HH", 14, 64) + bytes(32) + bytes([1])*32, "absent", True),
    (struct.pack("<HH", 1, 108) + bytes(10), "unknown", None),
])
def test_inspection(ext, fee, hook):
    raw = bytearray(82)
    raw[45] = 1
    if ext:
        raw.extend(bytes(83) + bytes([1]) + ext)
    value = dict(owner=TOKEN_2022_PROGRAM_ID, executable=False,
                 data=[base64.b64encode(raw).decode(), "base64"])
    row = inspect_account(dict(result=dict(context=dict(slot=7), value=value)), "finalized")
    assert row["transfer_fee"] == fee
    assert row["sell_restrictions"] == "unknown"
    if hook:
        assert row["hook_program"]
    if fee == "configured":
        assert row["fee_config"]["newer"]["basis_points"] == 25


@pytest.mark.parametrize("stamp,claims,code", [
    (1, [], "stale_read"),
    (999, [("goplus","hook_present",True)], "evidence_conflict"),
    (999, [("goplus","sell_blocked",False),("simulation","sell_blocked",True)], "evidence_conflict"),
])
def test_rejection_keeps_context(stamp, claims, code):
    raw = bytearray(82)
    raw[45] = 1
    account = dict(owner=TOKEN_2022_PROGRAM_ID, executable=False,
                   data=[base64.b64encode(raw).decode(), "base64"])
    row = inspect_account({"result":{"context":{"slot":7},"value":account}}, "finalized",
                          stamp, mint="m", now=1000, evidence=[
                              dict(source=s, field=f, value=v, observed_at=999, mint="m")
                              for s,f,v in claims])
    assert (row["error_code"], row["slot"], row["observed_at"]) == (code,7,stamp)
