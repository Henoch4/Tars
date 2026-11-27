"""Capture a coherent finalized route snapshot or fail closed."""
from __future__ import annotations
import json, urllib.request


def rpc(url: str, method: str, params: list) -> dict:
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    with urllib.request.urlopen(urllib.request.Request(url,data=body,headers={"Content-Type":"application/json"}),timeout=20) as r:
        return json.load(r)


def fetch_batches(url: str, addresses: list[str], batch_size: int = 100) -> tuple[int, list[dict]]:
    """Require every finalized batch to share one context slot."""
    rows=[]; context_slot=None
    for start in range(0,len(addresses),batch_size):
        batch=addresses[start:start+batch_size]
        response=rpc(url,"getMultipleAccounts",[batch,{"encoding":"base64","commitment":"finalized"}])
        result=response.get("result") or {}; slot=(result.get("context") or {}).get("slot")
        if not isinstance(slot,int): raise RuntimeError("batch missing context slot")
        if context_slot is None: context_slot=slot
        elif slot != context_slot: raise RuntimeError("incoherent batch context slot")
        values=result.get("value")
        if not isinstance(values,list) or len(values)!=len(batch): raise RuntimeError("batch result length mismatch")
        rows.extend({"address":a,"value":v,"slot":slot} for a,v in zip(batch,values))
    if context_slot is None: raise RuntimeError("empty snapshot")
    return context_slot, rows
