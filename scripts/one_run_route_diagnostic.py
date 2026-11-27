"""One-shot read-only Jupiter route diagnostic; never signs or broadcasts."""
import base64, json, os, sys, urllib.parse, urllib.request
from solders.transaction import VersionedTransaction
from solders.pubkey import Pubkey
from solders.pubkey import Pubkey

def http(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)

def main():
    mint, wallet = sys.argv[1:3]; rpc = os.environ["HELIUS_RPC_URL"]
    q = http("https://api.jup.ag/swap/v2/order?" + urllib.parse.urlencode({"inputMint":"So11111111111111111111111111111111111111112","outputMint":mint,"amount":"1000000","taker":wallet,"slippageBps":"100"}))
    raw = base64.b64decode(q["transaction"]); tx = VersionedTransaction.from_bytes(raw); msg = tx.message
    tables=[]
    for lookup in getattr(msg,"address_table_lookups",()):
        ar=http(rpc,"POST",{"jsonrpc":"2.0","id":2,"method":"getAccountInfo","params":[str(lookup.account_key),{"encoding":"base64","commitment":"finalized"}]})
        val=((ar.get("result") or {}).get("value"))
        if val and val.get("data"):
            data=base64.b64decode(val["data"][0]); tables.append({"address":str(lookup.account_key),"context_slot":((ar.get("result") or {}).get("context") or {}).get("slot"),"addresses":[str(Pubkey.from_bytes(data[o:o+32])) for o in range(56,len(data)-31,32)]})
    sim = http(rpc, "POST", {"jsonrpc":"2.0","id":1,"method":"simulateTransaction","params":[base64.b64encode(raw).decode(), {"encoding":"base64","sigVerify":False,"replaceRecentBlockhash":False,"commitment":"finalized","innerInstructions":True}]})
    keys=[str(k) for k in msg.account_keys]; refs=[]
    for i,ix in enumerate(msg.instructions): refs.append({"instruction":i,"program":keys[ix.program_id_index] if ix.program_id_index < len(keys) else None,"accounts":[keys[j] if j < len(keys) else None for j in ix.accounts]})
    result=sim.get("result",{}).get("value",{})
    resolved=[]
    for lookup in getattr(msg,"address_table_lookups",()):
        t=next((x for x in tables if x["address"]==str(lookup.account_key)),None)
        if t:
            resolved += [t["addresses"][i] for i in list(lookup.writable_indexes)+list(lookup.readonly_indexes) if i < len(t["addresses"])]
    null_accounts=sorted({a for r in refs for a in r["accounts"] if a is None})
    print(json.dumps({"message_accounts":len(keys),"lookup_tables":[{"address":t["address"],"context_slot":t["context_slot"],"address_count":len(t["addresses"])} for t in tables],"resolved_lookup_count":len(resolved),"instructions":refs,"null_static_refs":null_accounts,"simulation":{"top_level_error":sim.get("error"),"err":result.get("err"),"logs":result.get("logs"),"units_consumed":result.get("unitsConsumed"),"loaded_addresses":result.get("loadedAddresses"),"pre_token_balances":result.get("preTokenBalances"),"post_token_balances":result.get("postTokenBalances")},"request_id":q.get("requestId"),"route_plan":q.get("routePlan")}, separators=(",",":")))
if __name__ == "__main__": main()
