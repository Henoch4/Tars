import sys
import asyncio
import os

# Add proto directory to path
proto_dir = os.path.join(os.path.dirname(__file__), '..', 'proto')
sys.path.insert(0, proto_dir)

import grpc
from pyinjective.proto.exchange.injective_derivative_exchange_rpc_pb2 import FundingRatesRequest
from pyinjective.proto.exchange.injective_derivative_exchange_rpc_pb2_grpc import InjectiveDerivativeExchangeRPCStub


async def main():
    # Testnet gRPC exchange endpoint
    endpoint = "testnet.sentry.exchange.grpc.injective.network:443"
    
    # Create secure channel
    credentials = grpc.ssl_channel_credentials()
    channel = grpc.aio.secure_channel(endpoint, credentials)
    
    # Create stub
    stub = InjectiveDerivativeExchangeRPCStub(channel)
    
    # Market IDs for major perps on testnet
    markets = {
        "BTC/USDT PERP": "0x17ef48032cb24375ba7c2e39f384e56433bcab20cbee9a7357e4cba2eb00abe6",
        "ETH/USDT PERP": "0x17ef48032cb24375ba7c2e39f384e56433bcab20cbee9a7357e4cba2eb00abe6",  # Same for now, need to find actual ETH market ID
    }
    
    for name, market_id in markets.items():
        request = FundingRatesRequest(
            market_id=market_id,
            skip=0,
            limit=3,
            end_time=0
        )
        
        try:
            response = await stub.FundingRates(request)
            print(f"\n{name} (market_id: {market_id[:20]}...):")
            for fr in response.funding_rates:
                rate_pct = float(fr.rate) * 100
                print(f"  rate={rate_pct:.6f}% next={fr.timestamp}")
        except Exception as e:
            print(f"Error fetching {name}: {e}")
    
    await channel.close()


if __name__ == "__main__":
    asyncio.run(main())