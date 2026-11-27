"""Capture finalized executable-program provenance."""
from __future__ import annotations
import base64, hashlib, json, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.gta_mint_calibration import _load_dotenv, _rpc_url
from solders.pubkey import Pubkey
UPGRADEABLE_LOADER="BPFLoaderUpgradeab1e11111111111111111111111"
NATIVE_LOADER="NativeLoader1111111111111111111111111111111"
def rpc(url, method, params):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    with urllib.request.urlopen(urllib.request.Request(url,data=body,headers={"Content-Type":"application/json"}),timeout=20) as r:return json.load(r)
def main():
    manifest=json.loads(Path(sys.argv[1]).read_text()); _load_dotenv(Path('/etc/tars/agent.env') if Path('/etc/tars/agent.env').exists() else Path('.env')); url=_rpc_url(); rows=[]
    for address in manifest.get('required_programs',[]):
        if address == NATIVE_LOADER:
            rows.append({'address':address,'status':'runtime_dependency','runtime_provided':True,
                         'loader_identity':NATIVE_LOADER,'validator_version':manifest.get('runtime_version'),
                         'compatibility':False,'retrieved_at':__import__('time').time()})
            continue
        result=rpc(url,'getAccountInfo',[address,{'encoding':'base64','commitment':'finalized'}]).get('result',{}); value=result.get('value')
        if not isinstance(value,dict): rows.append({'address':address,'status':'unknown','reason':'missing_program_account'}); continue
        data=value.get('data'); raw=base64.b64decode(data[0]) if isinstance(data,list) and data else b''
        row={'address':address,'status':'ok','owner':value.get('owner'),'executable':value.get('executable'),'loader':value.get('owner'),'data_hash':hashlib.sha256(raw).hexdigest(),'data_length':len(raw),'slot':result.get('context',{}).get('slot'),'commitment':'finalized','programdata_status':'not_applicable'}
        if address==NATIVE_LOADER: row.update(status='runtime_dependency',runtime_dependency=True,runtime_version=manifest.get('runtime_version'))
        elif value.get('owner')==UPGRADEABLE_LOADER:
            pda,_=Pubkey.find_program_address([bytes(Pubkey.from_string(address))],Pubkey.from_string(UPGRADEABLE_LOADER)); row['programdata_address']=str(pda)
            pdata=rpc(url,'getAccountInfo',[str(pda),{'encoding':'base64','commitment':'finalized'}]).get('result',{}).get('value')
            if isinstance(pdata,dict) and isinstance(pdata.get('data'),list):
                pr=base64.b64decode(pdata['data'][0]); tag=int.from_bytes(pr[:4],'little') if len(pr)>=4 else -1; deploy_slot=int.from_bytes(pr[4:12],'little') if len(pr)>=12 else None; option=int.from_bytes(pr[12:16],'little') if len(pr)>=16 else -1; code=pr[48 if option==1 else 16:]
                row.update(programdata_status='ok' if tag==3 else 'malformed',deployment_slot=deploy_slot,upgrade_authority_present=option==1,deployed_bytecode_length=len(code),deployed_bytecode_hash=hashlib.sha256(code).hexdigest())
            else: row['programdata_status']='missing'
        rows.append(row)
    complete=all(r.get('status') in ('ok','runtime_dependency') and (r.get('status')=='runtime_dependency' or r.get('programdata_status')=='ok') for r in rows)
    print(json.dumps({'programs':rows,'complete':complete},sort_keys=True))
if __name__=='__main__': main()
