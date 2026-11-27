#!/bin/sh
set -u
ledger=/opt/tars/replay-ledger
log=/tmp/tars-replay-validator.log
rm -rf "$ledger"
mkdir -p "$ledger"
df -h /opt/tars
free -h
stat -c '%A %U:%G %n' /opt/tars/replay-20261120 /opt/tars/replay-20261120/account-load
/root/.local/share/solana/install/active_release/bin/solana-test-validator --ledger "$ledger" --account-dir /opt/tars/replay-20261120/account-load --reset --gossip-port 18000 --dynamic-port-range 18001-18027 --rpc-port 18899 --faucet-port 19900 >"$log" 2>&1 &
pid=$!
echo PID=$pid
opened=0
i=0
while [ "$i" -lt 60 ]; do
  if curl -fsS http://127.0.0.1:18899 -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","id":1,"method":"getVersion"}' >/tmp/replay-version.json 2>/dev/null; then opened=1; break; fi
  if ! kill -0 "$pid" 2>/dev/null; then break; fi
  i=$((i+1)); sleep 1
done
echo RPC_OPEN=$opened ELAPSED=$i
cat /tmp/replay-version.json 2>/dev/null || true
ps -p "$pid" -o pid,stat,etime,args 2>/dev/null || true
ss -ltnp 2>/dev/null | grep ':8899' || true
if kill -0 "$pid" 2>/dev/null; then kill "$pid"; fi
wait "$pid"; code=$?
echo EXIT_CODE=$code
echo LOG_START
cat "$log"
