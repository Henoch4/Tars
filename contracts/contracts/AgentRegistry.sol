// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

/// @title AgentRegistry — on-chain capability/pricing/reputation surface (I1).
/// @notice Self-registration: records are keyed by msg.sender, so the act of
/// transacting is the auth (no signature scheme, no owner bottleneck, no
/// relayers). Reputation is a VIEW over TradeAuditTrail.getAgentDailyStats —
/// derived from real trading history, unfakeable except by actually trading
/// (which costs gas and is itself risk-gated).
interface ITradeAuditTrail {
    function getAgentDailyStats(address agent)
        external
        view
        returns (uint256 volume, uint256 loss, uint256 trades);
    function killSwitchActive(address agent) external view returns (bool);
}

contract AgentRegistry {
    struct AgentRecord {
        string endpoint;
        bytes32 capabilitiesHash;
        string priceTiersVersion;
        uint256 updatedAt;
        bool active;
    }

    ITradeAuditTrail public immutable auditTrail;

    mapping(address => AgentRecord) public agents;

    event AgentRegistered(
        address indexed agent,
        string endpoint,
        bytes32 capabilitiesHash,
        string priceTiersVersion
    );
    event AgentUpdated(
        address indexed agent,
        string endpoint,
        bytes32 capabilitiesHash,
        string priceTiersVersion
    );
    event AgentDeactivated(address indexed agent);

    error EmptyEndpoint();
    error AlreadyRegistered();
    error NotRegistered();
    error InvalidAuditTrail();

    constructor(address auditTrail_) {
        if (auditTrail_ == address(0)) revert InvalidAuditTrail();
        auditTrail = ITradeAuditTrail(auditTrail_);
    }

    /// @notice First-time self-registration. Double-register reverts — use
    /// update for rotation (endpoint moves, capability set changes).
    function register(
        string calldata endpoint,
        bytes32 capabilitiesHash,
        string calldata priceTiersVersion
    ) external {
        if (bytes(endpoint).length == 0) revert EmptyEndpoint();
        if (agents[msg.sender].active) revert AlreadyRegistered();
        agents[msg.sender] = AgentRecord({
            endpoint: endpoint,
            capabilitiesHash: capabilitiesHash,
            priceTiersVersion: priceTiersVersion,
            updatedAt: block.timestamp,
            active: true
        });
        emit AgentRegistered(
            msg.sender,
            endpoint,
            capabilitiesHash,
            priceTiersVersion
        );
    }

    /// @notice Rotate endpoint / capabilities / price tiers. Inactive agents
    /// must re-register (keeps the event trail unambiguous).
    function update(
        string calldata endpoint,
        bytes32 capabilitiesHash,
        string calldata priceTiersVersion
    ) external {
        if (bytes(endpoint).length == 0) revert EmptyEndpoint();
        if (!agents[msg.sender].active) revert NotRegistered();
        agents[msg.sender] = AgentRecord({
            endpoint: endpoint,
            capabilitiesHash: capabilitiesHash,
            priceTiersVersion: priceTiersVersion,
            updatedAt: block.timestamp,
            active: true
        });
        emit AgentUpdated(
            msg.sender,
            endpoint,
            capabilitiesHash,
            priceTiersVersion
        );
    }

    /// @notice Voluntary delist. The record stays queryable (endpoint +
    /// history) but active=false; reputation view still reflects the audit
    /// trail either way.
    function deactivate() external {
        if (!agents[msg.sender].active) revert NotRegistered();
        agents[msg.sender].active = false;
        agents[msg.sender].updatedAt = block.timestamp;
        emit AgentDeactivated(msg.sender);
    }

    function isActive(address agent) external view returns (bool) {
        return agents[agent].active;
    }

    /// @notice Reputation derived live from the audit trail: cumulative
    /// volume/loss/trades plus the kill-switch flag, plus registry status.
    /// Nothing here is self-reported — registration advertises, history
    /// judges.
    function reputation(address agent)
        external
        view
        returns (
            uint256 volume,
            uint256 loss,
            uint256 trades,
            bool killActive,
            bool registered,
            bool active
        )
    {
        (volume, loss, trades) = auditTrail.getAgentDailyStats(agent);
        killActive = auditTrail.killSwitchActive(agent);
        AgentRecord storage rec = agents[agent];
        registered = bytes(rec.endpoint).length > 0;
        active = rec.active;
    }
}
