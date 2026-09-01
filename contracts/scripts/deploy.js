// Deployment script for TradeAuditTrail.sol
// Run with: npx hardhat run scripts/deploy.js --network xltestnet

const hre = require("hardhat");

async function main() {
  console.log("Deploying TradeAuditTrail contract...");

  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);
  // ethers v6: signer.getBalance() was removed — query the provider instead.
  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log("Account balance:", hre.ethers.formatEther(balance), "ETH");

  const TradeAuditTrail = await hre.ethers.getContractFactory("TradeAuditTrail");
  const contract = await TradeAuditTrail.deploy();
  await contract.waitForDeployment();

  const address = await contract.getAddress();
  console.log("TradeAuditTrail deployed to:", address);
  console.log("Block explorer: https://www.okx.com/explorer/xlayerTestnet/address/" + address);

  // Persist the deployment in the same shape the Python deploy path writes,
  // so both toolchains produce compatible artifacts.
  const fs = require("fs");
  const deployment = {
    contract: "TradeAuditTrail",
    address: address,
    chain_id: hre.network.config.chainId,
    deployed_at: new Date().toISOString(),
    deployer: deployer.address,
  };
  fs.writeFileSync(
    __dirname + "/../deployment.json",
    JSON.stringify(deployment, null, 2)
  );
  console.log("Deployment written to contracts/deployment.json");

  // Verify on Etherscan equivalent (OKX Explorer)
  if (process.env.VERIFY_CONTRACT === "true") {
    console.log("Waiting for block confirmation...");
    await new Promise(resolve => setTimeout(resolve, 15000));
    await hre.run("verify:verify", {
      address: address,
      constructorArguments: [],
    });
    console.log("Contract verified on explorer");
  }
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
