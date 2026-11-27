import json, urllib.request, sys
u='http://127.0.0.1:18899'
programs=sys.argv[1:]
def rpc(addr):
    body=json.dumps({'jsonrpc':'2.0','id':1,'method':'getAccountInfo','params':[addr,{'encoding':'base64','commitment':'processed'}]}).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(u,data=body,headers={'Content-Type':'application/json'}),timeout=5)).get('result',{}).get('value')
rows=[]
for a in programs:
    v=rpc(a); rows.append({'address':a,'exists':isinstance(v,dict),'executable':v.get('executable') if isinstance(v,dict) else None,'owner':v.get('owner') if isinstance(v,dict) else None,'data_length':len(v.get('data',[b''])[0]) if isinstance(v,dict) and isinstance(v.get('data'),list) else None})
print(json.dumps(rows,sort_keys=True))
