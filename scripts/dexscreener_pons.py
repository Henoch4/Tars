import json
import urllib.request
from datetime import datetime, timezone

# PONS token contract address on Robinhood Chain (verified via DexScreener)
PONS_TOKEN_ADDRESS = "0x39dBED3a2bd333467115dE45665cC57F813C4571"
ROBINHOOD_CHAIN_ID = "robinhood"

# Timestamp for reproducibility
retrieval_ts = datetime.now(timezone.utc).isoformat()

print(f"PONS Token Pair Listing — Robinhood Chain")
print(f"Retrieved: {retrieval_ts}")
print(f"Token: Pons (PONS) — {PONS_TOKEN_ADDRESS}")
print(f"Chain: Robinhood Chain (chainId: {ROBINHOOD_CHAIN_ID})")
print()

# Fetch all pairs for the PONS token contract (chain-specific endpoint)
req = urllib.request.Request(
    f"https://api.dexscreener.com/latest/dex/tokens/{PONS_TOKEN_ADDRESS}",
    headers={"User-Agent": "Mozilla/5.0"},
    method="GET"
)

try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
except Exception as e:
    print(f"Error fetching PONS token pairs: {e}")
    data = None

if data:
    pairs = data.get("pairs", [])
    
    # Filter for Robinhood Chain chainId AND deduplicate by pair address
    robinhood_pairs = []
    seen_pair_addresses = set()
    
    for pair in pairs:
        chain_id = pair.get("chainId")
        pair_address = pair.get("pairAddress")
        
        if chain_id != ROBINHOOD_CHAIN_ID:
            continue
        if pair_address in seen_pair_addresses:
            continue
        seen_pair_addresses.add(pair_address)
        
        # Verify base token matches PONS contract
        base_token = pair.get("baseToken", {})
        if base_token.get("address", "").lower() != PONS_TOKEN_ADDRESS.lower():
            continue
            
        robinhood_pairs.append(pair)
    
    print(f"Total pairs returned by API: {len(pairs)}")
    print(f"Pairs on Robinhood Chain: {len([p for p in pairs if p.get('chainId') == ROBINHOOD_CHAIN_ID])}")
    print(f"Unique PONS pairs after dedup: {len(robinhood_pairs)}")
    print()
    
    for i, p in enumerate(robinhood_pairs):
        base = p.get("baseToken", {})
        quote = p.get("quoteToken", {})
        price_usd = p.get("priceUsd")
        volume = p.get("volume", {})
        liquidity = p.get("liquidity", {})
        price_change = p.get("priceChange", {})
        txns = p.get("txns", {})
        
        print(f"  Pair {i+1}: {base.get('symbol')} / {quote.get('symbol')}")
        print(f"  Pair address: {p.get('pairAddress')}")
        print(f"  Base token: {base.get('name')} ({base.get('symbol')}) — {base.get('address')}")
        print(f"  Price: ${price_usd}")
        print(f"  Volume 24h: ${volume.get('h24', 0):,.2f}")
        print(f"  Liquidity: ${liquidity.get('usd', 0):,.2f}")
        print(f"  Price change h24: {price_change.get('h24', 'N/A')}%")
        print(f"  Price change h6: {price_change.get('h6', 'N/A')}%")
        print(f"  Txns h24 buys: {txns.get('h24', {}).get('buys', 'N/A')}, sells: {txns.get('h24', {}).get('sells', 'N/A')}")
        print(f"  Pair created: {p.get('pairCreatedAt', 'N/A')}")
        print(f"  DexUrl: {p.get('url', 'N/A')}")
        print()
