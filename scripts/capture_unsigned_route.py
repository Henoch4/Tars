"""Capture one unsigned Jupiter route artifact for deterministic review."""
from __future__ import annotations
import base64, hashlib, json, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.meme_execute import _http, SWAP_BASE
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned

def main():
    mint, wallet, snapshot_checksum, out_path = sys.argv[1:5]
    amount = 1_000_000
    sol = 'So11111111111111111111111111111111111111112'
    quote = _http('GET', SWAP_BASE + '/order?' + urllib.parse.urlencode({'inputMint':sol,'outputMint':mint,'amount':amount,'taker':wallet,'slippageBps':100}))
    raw = base64.b64decode(quote['transaction']); tx = VersionedTransaction.from_bytes(raw); msg = tx.message
    full_hash = hashlib.sha256(bytes(to_bytes_versioned(msg))).hexdigest()
    structural = {'account_keys':[str(k) for k in msg.account_keys], 'instructions':[{'program':ix.program_id_index,'accounts':list(ix.accounts),'data':bytes(ix.data).hex()} for ix in msg.instructions], 'lookups':[str(x.account_key) for x in getattr(msg,'address_table_lookups',())]}
    structural_hash = hashlib.sha256(json.dumps(structural,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    artifact={'candidate_mint':mint,'input_lamports':amount,'quote_id':quote.get('requestId'),'route_id':quote.get('routePlan'),'serialized_transaction':base64.b64encode(raw).decode(),'recent_blockhash':str(msg.recent_blockhash),'lookup_table_addresses':[str(x.account_key) for x in getattr(msg,'address_table_lookups',())],'captured_at':time.time(),'capture_source':'jupiter','snapshot_checksum':snapshot_checksum,'message_hash':full_hash,'structural_route_hash':structural_hash}
    Path(out_path).write_text(json.dumps(artifact,sort_keys=True,indent=2),encoding='utf-8'); print(json.dumps({'artifact':out_path,'message_hash':full_hash,'structural_route_hash':structural_hash,'lookup_tables':len(artifact['lookup_table_addresses'])}))
if __name__=='__main__': main()
