#!/bin/sh
set -u
rm -rf /opt/tars/replay-ledger
mkdir -p /opt/tars/replay-ledger
/root/.local/share/solana/install/active_release/bin/solana-test-validator --ledger /opt/tars/replay-ledger --account-dir /opt/tars/replay-20261120/account-load --reset --gossip-port 18000 --dynamic-port-range 18001-18027 --rpc-port 18899 --faucet-port 19900 --quiet >/tmp/tars-replay-validator.log 2>&1 &
pid=$!
sleep 2
python3 - <<'PY'
import glob,json,urllib.request
u='http://127.0.0.1:18899'
def rpc(m,p=[]):
 b=json.dumps({'jsonrpc':'2.0','id':1,'method':m,'params':p}).encode(); return json.load(urllib.request.urlopen(urllib.request.Request(u,data=b,headers={'Content-Type':'application/json'}),timeout=10)).get('result')
files=glob.glob('/opt/tars/replay-20261120/account-load/*.json'); rows=[]
for f in files:
 d=json.load(open(f)); a=d['pubkey']; v=rpc('getAccountInfo',[a,{'encoding':'base64','commitment':'finalized'}])['value']; raw=v['data'][0] if isinstance(v,dict) else ''; rows.append({'address':a,'match':isinstance(v,dict) and v.get('lamports')==d['account']['lamports'] and v.get('owner')==d['account']['owner'] and v.get('executable')==d['account']['executable'] and raw==d['account']['data'][0]})
print(json.dumps({'runtime':rpc('getVersion'),'slot':rpc('getSlot'),'account_count':len(rows),'accounts_match':all(x['match'] for x in rows),'mismatches':[x['address'] for x in rows if not x['match']],'endpoint':u}))
PY
kill "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
