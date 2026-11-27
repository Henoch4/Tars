import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.gta_external_safety import GoplusSolanaClient

payload = GoplusSolanaClient().fetch(sys.argv[1])
print("root_keys=" + ",".join(sorted(payload.keys())) if isinstance(payload, dict) else "root_type=" + type(payload).__name__)
if isinstance(payload, dict):
    print("code=" + str(payload.get("code")))
    print("message=" + str(payload.get("message")))
    for key in ("result", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            print(key + "_keys=" + ",".join(sorted(value.keys())))
        elif value is not None:
            print(key + "_type=" + type(value).__name__)
