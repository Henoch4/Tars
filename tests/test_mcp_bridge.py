"""I13 regression: thin MCP client bridge speaks NDJSON JSON-RPC and
fails closed with typed reasons. A fake in-process server stands in for
any external research tool — no network, no `mcp` dependency."""
import json
import sys

import pytest

from src.mcp_bridge import McpBridge, McpBridgeConfig, McpBridgeError


FAKE_SERVER = r"""
import json, sys, time
mode = sys.argv[1] if len(sys.argv) > 1 else "good"

def reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method", "")
    rid = msg.get("id")
    if method == "initialize":
        reply(rid, {"protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "fake", "version": "0"}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        if mode == "badshape":
            reply(rid, {"nope": 1})
        elif mode == "garbage":
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()
        else:
            reply(rid, {"tools": [{"name": "quote", "description": "d",
                                   "inputSchema": {"type": "object"}}]})
    elif method == "tools/call":
        name = msg.get("params", {}).get("name", "")
        if mode == "slow":
            time.sleep(30)
        if name == "fail":
            reply(rid, {"isError": True, "content": [{"type": "text",
                                                     "text": "nope"}]})
        else:
            reply(rid, {"content": [{"type": "text", "text": "42"}]})
"""


@pytest.fixture(scope="module")
def server_path():
    # NOTE: pytest's tmp_path fixture is broken on this machine right now
    # (WinError 5 on pytest-of-Henoch); tmp_path_factory hits the same
    # machinery, so use tempfile directly. The server file is written once
    # per module — cheaper and immune to the fixture bug.
    import tempfile
    import os
    d = tempfile.mkdtemp(prefix="mcp_bridge_test_")
    p = os.path.join(d, "fake_mcp_server.py")
    with open(p, "w") as f:
        f.write(FAKE_SERVER)
    return p


def _bridge(server_path, mode="good", **overrides):
    cfg = McpBridgeConfig(
        command=[sys.executable, server_path, mode],
        timeout_s=overrides.pop("timeout_s", 15.0),
        server_name="fake",
        **overrides,
    )
    return McpBridge(cfg)


class TestBridgeRoundtrip:
    def test_list_and_call(self, server_path):
        with _bridge(server_path) as b:
            assert b.running is True
            tools = b.list_tools()
            assert [t.name for t in tools] == ["quote"]
            assert tools[0].input_schema == {"type": "object"}
            out = b.call_tool("quote", {"symbol": "BTC"})
            assert out["content"][0]["text"] == "42"
        assert b.running is False

    def test_server_tool_error_is_typed(self, server_path):
        with _bridge(server_path) as b:
            with pytest.raises(McpBridgeError) as exc:
                b.call_tool("fail", {})
            assert exc.value.reason == "tool_error"


class TestBridgeFailClosed:
    def test_spawn_failure(self):
        b = McpBridge(McpBridgeConfig(command=["definitely-not-a-binary-xyz"]))
        with pytest.raises(McpBridgeError) as exc:
            b.start()
        assert exc.value.reason == "spawn_failed"

    def test_call_before_start(self, server_path):
        b = _bridge(server_path)
        with pytest.raises(McpBridgeError) as exc:
            b.list_tools()
        assert exc.value.reason == "not_started"

    def test_timeout(self, server_path):
        with _bridge(server_path, "slow", timeout_s=1.0) as b:
            with pytest.raises(McpBridgeError) as exc:
                b.call_tool("quote", {})
            assert exc.value.reason == "timeout"

    def test_bad_shape_is_protocol_error(self, server_path):
        with _bridge(server_path, mode="badshape") as b:
            with pytest.raises(McpBridgeError) as exc:
                b.list_tools()
            assert exc.value.reason == "protocol"

    def test_garbage_line_is_protocol_error(self, server_path):
        with _bridge(server_path, mode="garbage") as b:
            with pytest.raises(McpBridgeError) as exc:
                b.list_tools()
            assert exc.value.reason == "protocol"

    def test_unknown_reason_rejected(self):
        with pytest.raises(AssertionError):
            McpBridgeError("exploded", "x")
