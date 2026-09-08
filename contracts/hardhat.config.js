/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    // 0.8.30 — TradeAuditTrail.sol declares pragma ^0.8.30; 0.8.29 here made
    // `npx hardhat compile` fail on the main audit contract.
    version: "0.8.30",
    settings: {
      optimizer: { enabled: true, runs: 200 },
    },
  },
  networks: {
    xltestnet: {
      url: "https://testnet-rpc.xlayer.tech",
      chainId: 1952,
      accounts: process.env.PRIVATE_KEY ? [process.env.PRIVATE_KEY] : [],
      gasPrice: 1000000000,
    },
    xlmainnet: {
      url: "https://rpc.xlayer.tech",
      chainId: 196,
      accounts: process.env.PRIVATE_KEY ? [process.env.PRIVATE_KEY] : [],
      gasPrice: 1000000000,
    },
  },
  paths: {
    sources: "./contracts",
    tests: "./test",
    cache: "./cache",
    artifacts: "./artifacts",
  },
};
