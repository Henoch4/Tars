import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# PONS token contract address on Robinhood Chain (verified via DexScreener)
PONS_TOKEN_ADDRESS = "0x39dBED3a2bd333467115dE45665cC57F813C4571"
ROBINHOOD_CHAIN_ID = "robinhood"

# Timestamp for reproducibility
retrieval_ts = datetime.now(timezone.utc).isoformat()

print(f"PONS Token Research on Robinhood Chain")
print(f"Retrieved: {retrieval_ts}")
print(f"Token address: {PONS_TOKEN_ADDRESS}")
print(f"Chain: Robinhood Chain (dexscreener chainId: {ROBINHOOD_CHAIN_ID})")
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
    
    # Filter for Robinhood Chain chainId and deduplicate by pair address
    robinhood_pairs = []
    seen_pair_addresses = set()
    
    for pair in pairs:
        chain_id = pair.get("chainId")
        pair_address = pair.get("pairAddress")
        
        # Filter: must be on Robinhood Chain AND have a unique pair address
        if chain_id != ROBINHOOD_CHAIN_ID:
            continue
        if pair_address in seen_pair_addresses:
            continue
        seen_pair_addresses.add(pair_address)
        
        # Verify this pair's base token matches the PONS contract
        base_token = pair.get("baseToken", {})
        if base_token.get("address", "").lower() != PONS_TOKEN_ADDRESS.lower():
            continue
            
        robinhood_pairs.append(pair)
    
    print(f"Total pairs returned by API: {len(pairs)}")
    print(f"Pairs on Robinhood Chain (chainId={ROBINHOOD_CHAIN_ID}): {len([p for p in pairs if p.get('chainId') == ROBINHOOD_CHAIN_ID])}")
    print(f"Unique PONS pairs after dedup: {len(robinhood_pairs)}")
    print()
    
    # Aggregate metrics from unique Robinhood Chain PONS pairs
    total_vol_h24 = 0
    total_liq = 0
    pair_details = []
    
    for p in robinhood_pairs:
        base = p.get("baseToken", {})
        quote = p.get("quoteToken", {})
        vol = p.get("volume", {}).get("h24", 0)
        liq = p.get("liquidity", {}).get("usd", 0)
        price_change = p.get("priceChange", {})
        txns = p.get("txns", {})
        
        total_vol_h24 += vol
        total_liq += liq
        
        pair_details.append({
            "pair_address": p.get("pairAddress"),
            "base_token": base.get("symbol"),
            "quote_token": quote.get("symbol"),
            "price_usd": p.get("priceUsd"),
            "volume_h24_usd": vol,
            "liquidity_usd": liq,
            "price_change_h24_pct": price_change.get("h24"),
            "price_change_h6_pct": price_change.get("h6"),
            "txns_h24_buys": txns.get("h24", {}).get("buys"),
            "txns_h24_sells": txns.get("h24", {}).get("sells"),
            "pair_created_at": p.get("pairCreatedAt"),
            "dex_url": p.get("url"),
        })
        
        print(f"  {pair.get('pairAddress')[:12]}... {base.get('symbol')}/{quote.get('symbol')}: "
              f"vol=${vol:,.2f}, liq=${liq:,.2f}, h24={price_change.get('h24')}%, "
              f"buys={txns.get('h24', {}).get('buys')}, sells={txns.get('h24', {}).get('sells')}")
    
    print(f"\n=== Aggregate (Robinhood Chain PONS pairs only) ===")
    print(f"Unique pairs: {len(robinhood_pairs)}")
    print(f"Total 24h volume: ${total_vol_h24:,.2f}")
    print(f"Total liquidity: ${total_liq:,.2f}")
    
    # Save raw response for audit trail
    raw_response = {
        "retrieval_ts_utc": retrieval_ts,
        "token_address": PONS_TOKEN_ADDRESS,
        "chain_id": ROBINHOOD_CHAIN_ID,
        "unique_pair_count": len(robinhood_pairs),
        "total_volume_h24_usd": total_vol_h24,
        "total_liquidity_usd": total_liq,
        "pair_details": pair_details,
    }
    
    output_path = Path(r"C:\Users\Henoch\AppData\Local\Temp\opencode\pons_robinhood_raw.json")
    output_path.write_text(json.dumps(raw_response, indent=2), encoding="utf-8")
    print(f"\nRaw response saved to {output_path}")
else:
    print("Failed to fetch PONS token data from DexScreener")
