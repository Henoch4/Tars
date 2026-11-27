import json, urllib.request
rows=[l.split('=',1)[1].strip() for l in open('/etc/tars/agent.env') if l.startswith('ALCHEMY_SOLANA_RPC_URL=')]
if not rows: print('ALCHEMY_URL_MISSING'); raise SystemExit
body=json.dumps({'jsonrpc':'2.0','id':1,'method':'getVersion','params':[]}).encode()
req=urllib.request.Request(rows[0],data=body,headers={'Content-Type':'application/json'})
x=json.load(urllib.request.urlopen(req,timeout=15))
print(json.dumps({'ok':'result' in x,'result':x.get('result'),'error_code':(x.get('error') or {}).get('code')}))
