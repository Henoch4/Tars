"""Read-only adapter for Rewind-style historical Solana simulation services."""
from __future__ import annotations
import json, os, time, urllib.request
from typing import Any, cast
from .gta_external_safety import AuthoritativeSellabilityEvidence

class RewindClient:
    def __init__(self, token: str | None = None, endpoint: str | None = None, timeout_ms: int = 10000, opener=None):
        raw_token = token if token is not None else os.getenv("REWIND_API_KEY", "")
        raw_endpoint = endpoint or os.getenv("REWIND_ENDPOINT", "https://rewind.solanavibestation.com")
        self.token = (raw_token or "").strip()
        self.endpoint = (raw_endpoint or "").strip()
        self.timeout_s = max(0.1, timeout_ms / 1000)
        self._opener = opener or urllib.request.urlopen

    def simulate(self, transaction_b64: str, *, anchor: dict[str, Any], test_conditions: str) -> AuthoritativeSellabilityEvidence:
        observed = time.time()
        if not self.token:
            return AuthoritativeSellabilityEvidence("rewind", "provider_error", observed, test_conditions=test_conditions, reason="provider token not configured")
        payload = {"jsonrpc":"2.0", "id":1, "method":"simulateHistoricalTransaction", "params":[transaction_b64, {"anchor":anchor, "innerInstructions":True}]}
        try:
            req = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json", "Authorization":f"Bearer {self.token}"}, method="POST")
            with self._opener(req, timeout=self.timeout_s) as response: body=json.loads(response.read().decode())
        except Exception as exc:
            return AuthoritativeSellabilityEvidence("rewind", "provider_error", observed, test_conditions=test_conditions, reason=type(exc).__name__)
        if not isinstance(body, dict) or body.get("error"):
            return AuthoritativeSellabilityEvidence("rewind", "provider_error", observed, test_conditions=test_conditions, reason="rpc_error")
        raw_result = body.get("result")
        result: dict[str, Any] = cast(dict[str, Any], raw_result) if isinstance(raw_result, dict) else {}
        raw_value = result.get("value")
        value: dict[str, Any] = cast(dict[str, Any], raw_value) if isinstance(raw_value, dict) else result
        status="sellable" if value.get("err") in (None, False) else "not_sellable"
        slot=result.get("slot") or value.get("slot")
        return AuthoritativeSellabilityEvidence("rewind", status, observed, slot, test_conditions, "historical simulation completed")
