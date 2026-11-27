import base64,json,os,urllib.parse,urllib.request
wallet='Ehfkx3c8aGV6xGbvmSezHvBLt68ZwWdw1bvSXZShcwUu'; mint='BsbsB3WLq7vbY5En3MBCAsTrcwaCxNKY2Mp5pGuLpump'
def post(url,p):
 try:
  r=urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(p).encode(),headers={'Content-Type':'application/json'}),timeout=20); return json.load(r)
 except urllib.error.HTTPError as e:
  return {'error':{'code':e.code,'message':e.read().decode(errors='replace')[:200]}}
rpc=[l.split('=',1)[1].strip() for l in open('/etc/tars/agent.env') if l.startswith('ALCHEMY_SOLANA_RPC_URL=')][0]
qurl='https://api.jup.ag/swap/v2/order?'+urllib.parse.urlencode({'inputMint':'So11111111111111111111111111111111111111112','outputMint':mint,'amount':'1000000','taker':wallet,'slippageBps':'100'})
q=json.load(urllib.request.urlopen(qurl,timeout=20)); slot=post(rpc,{'jsonrpc':'2.0','id':1,'method':'getSlot','params':[{'commitment':'finalized'}]}).get('result'); tx=q.get('transaction')
p={'jsonrpc':'2.0','id':2,'method':'simulateTransactionsAtSlot','params':[{'simulations':[{'slot':slot,'encodedTransaction':tx}], 'postExecutionAccountsConfigs':{'accounts':[],'encoding':'base64'}}]}
x=post(rpc,p); print(json.dumps({'slot':slot,'method_available':'error' not in x,'error_code':(x.get('error') or {}).get('code'),'error_message':(x.get('error') or {}).get('message'),'result_shape':type(x.get('result')).__name__}))
