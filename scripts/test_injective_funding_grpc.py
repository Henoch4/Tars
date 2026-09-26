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
    
    # Market ID for BTC/USDT PERP on testnet (from example)
    market_id = "0x17ef48032cb24375ba7c2e39f384e56433bcab20cbee9a7357e4cba2eb00abe6"
    
    # Create request
    request = FundingRatesRequest(
        market_id=market_id,
        skip=0,
        limit=3,
        end_time=0  # 0 means latest
    )
    
    try:
        # Call the gRPC method
        response = await stub.FundingRates(request)
        print("Funding Rates Response:")
        print(response)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await channel.close()


if __name__ == "__main__":
    asyncio.run(main())