"""Thin MCP client bridge (I13): TARS *consuming* external tools.

Lets the research surface call another agent's MCP server (market data,
sentiment, signal inputs) without vendoring a per-provider SDK. Deliberately
thin: stdio NDJSON JSON-RPC 2.0 over a spawned command, stdlib only (no `mcp`
dependency), three operations — start, list_tools, call_tool — with strict
timeouts and typed, fail-closed errors.

NOT wired into any live path: no external research input exists yet, and
wiring without a consumer would be dead code. When one arrives, it gets an
explicit call site plus a regression test here, not a silent import.

Protocol (MCP stdio transport): one JSON object per line, no framing.
Handshake: initialize -> notifications/initialized -> tools/list|call.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT_S = 30.0


class McpBridgeError(RuntimeError):
    """Typed failure. `reason` is a closed set (S5/W3 style)."""

    CLOSED_REASONS = frozenset({
        "spawn_failed",
        "timeout",
        "protocol",
        "transport_closed",
        "not_started",
        "tool_error",
    })

    def __init__(self, reason: str, detail: str = ""):
        assert reason in self.CLOSED_REASONS, f"unknown reason {reason!r}"
        self.reason = reason
        self.detail = detail
        super().__init__(f"mcp bridge [{reason}]: {detail}")


@dataclass
class McpBridgeConfig:
    """How to reach one external MCP server."""
    command: list[str]              # e.g. ["python", "research_server.py"]
    timeout_s: float = DEFAULT_TIMEOUT_S
    server_name: str = "external"   # log label only, never a metric label


@dataclass
class McpTool:
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)


class McpBridge:
    """One lived subprocess, sequential calls (no pipelining)."""

    def __init__(self, config: McpBridgeConfig):
        self.config = config
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Spawn the server and complete the MCP handshake."""
        if self.running:
            return
        try:
            self._proc = subprocess.Popen(
                self.config.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (OSError, FileNotFoundError) as e:
            raise McpBridgeError("spawn_failed", str(e))
        try:
            result = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "tars-mcp-bridge", "version": "0.1.0"},
            })
            if not isinstance(result, dict) or "serverInfo" not in result:
                raise McpBridgeError(
                    "protocol",
                    f"initialize missing serverInfo: {result!r:.200}",
                )
            self._notify("notifications/initialized", {})
        except McpBridgeError:
            self.stop()
            raise

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5.0)
        except Exception:
            proc.kill()
        try:
            err = proc.stderr.read() if proc.stderr else ""
            if err.strip():
                logger.warning(f"MCP server {self.config.server_name} stderr: "
                               f"{err.strip()[-500:]}")
        except Exception:
            pass

    def list_tools(self) -> list[McpTool]:
        """Tool catalog of the external server."""
        result = self._request("tools/list", {})
        try:
            raw = result["tools"]
            return [McpTool(name=t["name"],
                            description=t.get("description", ""),
                            input_schema=t.get("inputSchema", {}))
                    for t in raw]
        except (KeyError, TypeError, AttributeError) as e:
            raise McpBridgeError("protocol", f"bad tools/list shape: {e}")

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call one external tool. Server-side tool errors surface as
        McpBridgeError(tool_error), never as a fabricated result."""
        result = self._request("tools/call", {
            "name": name, "arguments": arguments or {},
        })
        if not isinstance(result, dict):
            raise McpBridgeError("protocol", f"tools/call not an object: {result!r:.200}")
        if result.get("isError"):
            raise McpBridgeError(
                "tool_error",
                str(result.get("content", ""))[:500],
            )
        return result

    # --- transport ---

    def _next_request_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _send(self, payload: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise McpBridgeError("transport_closed", str(e))

    def _recv(self) -> dict:
        assert self._proc is not None and self._proc.stdout is not None
        line = self._proc.stdout.readline()
        if not line:
            raise McpBridgeError("transport_closed", "server closed stdout")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            raise McpBridgeError("protocol", f"non-JSON line: {e}")
        if not isinstance(msg, dict):
            raise McpBridgeError("protocol", "message not an object")
        return msg

    def _request(self, method: str, params: dict) -> dict:
        """One request/response exchange. Sequential under a lock; the
        blocking read runs with a watchdog so a hung server cannot hang
        the caller past timeout_s (fail-closed)."""
        if not self.running:
            raise McpBridgeError("not_started", "call start() first")
        with self._lock:
            rid = self._next_request_id()
            self._send({"jsonrpc": "2.0", "id": rid,
                        "method": method, "params": params})
            box: dict = {}

            def _read_once() -> None:
                try:
                    box["msg"] = self._recv()
                except BaseException as e:  # noqa: BLE001 — re-raised below
                    box["error"] = e

            worker = threading.Thread(target=_read_once, daemon=True)
            worker.start()
            worker.join(timeout=self.config.timeout_s)
            if worker.is_alive():
                raise McpBridgeError(
                    "timeout",
                    f"{method} exceeded {self.config.timeout_s}s",
                )
            if "error" in box:
                raise box["error"]
            msg = box.get("msg", {})
            if msg.get("id") != rid:
                raise McpBridgeError(
                    "protocol", f"id mismatch: want {rid}, got {msg.get('id')}")
            if "error" in msg:
                raise McpBridgeError(
                    "protocol", f"server error: {msg['error']!r:.200}")
            return msg.get("result", {})

    def _notify(self, method: str, params: dict) -> None:
        if not self.running:
            raise McpBridgeError("not_started", "call start() first")
        with self._lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def __enter__(self) -> "McpBridge":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
