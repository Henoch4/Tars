"""Capture a finalized, immutable GTA replay manifest (read-only)."""
from __future__ import annotations
import base64, hashlib, json, sys, time
from pathlib import Path
import urllib.parse, urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.gta_mint_calibration import _load_dotenv, _rpc_url
from src.meme_execute import _http, SWAP_BASE
from solders.transaction import VersionedTransaction


def rpc(url, method, params):
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type":"application/json"}), timeout=20) as r:
        return json.load(r)


def main():
    mint = sys.argv[1]
    wallet = sys.argv[2]
    _load_dotenv(Path("/etc/tars/agent.env") if Path("/etc/tars/agent.env").exists() else Path(".env"))
    url = _rpc_url()
    slot = rpc(url, "getSlot", [{"commitment":"finalized"}])["result"]
    height = rpc(url, "getBlockHeight", [{"commitment":"finalized"}])["result"]
    genesis = rpc(url, "getGenesisHash", [])["result"]
    order = _http("GET", SWAP_BASE + "/order?" + urllib.parse.urlencode({
        "inputMint":"So11111111111111111111111111111111111111112",
        "outputMint":mint, "amount":1000000, "taker":wallet, "slippageBps":100}))
    tx = VersionedTransaction.from_bytes(base64.b64decode(order["transaction"]))
    keys = [str(k) for k in tx.message.account_keys]
    accounts = []
    for address in keys:
        info = rpc(url, "getAccountInfo", [address, {"encoding":"base64", "commitment":"finalized"}])
        value = info.get("result", {}).get("value")
        if value is None:
            continue
        data = value.get("data")
        encoded = data[0] if isinstance(data, list) and data else ""
        digest = hashlib.sha256(encoded.encode()).hexdigest() if isinstance(encoded, str) else None
        accounts.append({"address":address, "owner":value.get("owner"), "data_hash":digest,
                         "slot":info.get("result",{}).get("context",{}).get("slot"),
                         "source":"helius_rpc"})
    programs = sorted({a["owner"] for a in accounts if a.get("owner")})
    manifest = {"source_identity":"helius_rpc", "capture_slot":slot,
                "slot":slot, "block_height":height, "commitment":"finalized",
                "captured_at":time.time(), "observed_at":time.time(),
                "runtime_version":"solana-mainnet", "genesis_hash":genesis,
                "program_hashes":{p:None for p in programs}, "mint_account":mint,
                "pool_accounts":[], "required_programs":programs,
                "required_accounts":accounts, "mint":mint, "pool":"unknown",
                "programs":programs, "accounts":[a["address"] for a in accounts]}
    canonical=json.dumps(manifest, sort_keys=True, separators=(",",":"))
    manifest["snapshot_checksum"] = hashlib.sha256(canonical.encode()).hexdigest()
    print(json.dumps(manifest, sort_keys=True))

if __name__ == "__main__": main()
