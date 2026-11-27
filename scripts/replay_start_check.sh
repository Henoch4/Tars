#!/bin/sh
set -u
rm -rf /opt/tars/replay-20261120/account-load /opt/tars/replay-ledger
mkdir -p /opt/tars/replay-20261120/account-load /opt/tars/replay-ledger
find /opt/tars/replay-20261120/accounts -maxdepth 1 -name '*.json' ! -name 'bundle_manifest.json' -exec cp {} /opt/tars/replay-20261120/account-load/ \;
/root/.local/share/solana/install/active_release/bin/solana-test-validator --ledger /opt/tars/replay-ledger --account-dir /opt/tars/replay-20261120/account-load --reset --quiet >/tmp/tars-replay-validator.log 2>&1 &
pid=$!
sleep 12
curl -fsS http://127.0.0.1:8899 -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","id":1,"method":"getVersion"}' || true
kill "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
echo LOG
tail -n 30 /tmp/tars-replay-validator.log
