"""One-shot GoPlus app-key exchange; never prints credentials or token."""
from __future__ import annotations
import hashlib, json, os, sys, tempfile, time, urllib.error, urllib.request

def main(path: str) -> int:
    cfg = {}
    for line in open(path, encoding="utf-8"):
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1); cfg[k.strip()] = v.strip()
    key, secret = cfg.get("GOPLUS_APP_KEY", ""), cfg.get("GOPLUS_APP_SECRET", "")
    if not key or not secret:
        print("exchange_rejected:missing_credentials"); return 2
    ts = int(time.time())
    sign = hashlib.sha1((key + str(ts) + secret).encode()).hexdigest()
    req = urllib.request.Request(
        "https://api.gopluslabs.io/api/v1/token",
        # GoPlus integrations send the timestamp as a JSON string even though
        # the schema describes it as int64; preserve the exact wire form.
        data=json.dumps({"app_key": key, "sign": sign, "time": str(ts)}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"exchange_http_error:{exc.code}"); return 3
    except Exception as exc:
        print(f"exchange_transport_error:{type(exc).__name__}"); return 4
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        print("exchange_rejected:unexpected_schema"); return 5
    result = payload["result"]
    token = result.get("access_token")
    if not isinstance(token, str) or not token.strip():
        print("exchange_rejected:access_token_missing"); return 6
    env_path = "/etc/tars/agent.env"
    text = open(env_path, encoding="utf-8").read() if os.path.exists(env_path) else ""
    lines = [x for x in text.splitlines() if not x.startswith("GOPLUS_SOLANA_TOKEN=")]
    lines.append("GOPLUS_SOLANA_TOKEN=" + token.strip())
    fd, tmp = tempfile.mkstemp(prefix=".agent.env.", dir="/etc/tars", text=True)
    os.chmod(tmp, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n"); out.flush(); os.fsync(out.fileno())
    os.replace(tmp, env_path); os.chmod(env_path, 0o600)
    print("exchange_ok:token_written")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
