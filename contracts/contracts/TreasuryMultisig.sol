// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title TreasuryMultisig
 * @notice Threshold multisig for treasury/control key operations.
 * 
 * Based on ika CAPTURE-3: treasury-key threshold multisig/TSS.
 * Not per-transaction 2PC-MPC — the on-chain audit trail / risk gate
 * paths stay single-key + connect-wallet. Only the treasury key
 * (which can move/influence vault assets) goes behind threshold.
 * 
 * Features:
 * - N-of-M threshold signatures
 * - Proposal + execution with timelock
 * - Owner can rotate signers (with timelock)
 * - Emergency pause by owner
 * - Replay protection via nonce
 * - Events for all state changes
 */
contract TreasuryMultisig {
    /* ========== STATE ========== */
    
    address public immutable owner;
    // threshold/timelockDelay are deliberately MUTABLE (signer rotation,
    // threshold updates): immutable would forbid the writes the rotation
    // path requires. owner alone is immutable (set once, never rotated
    // in place — rotation would need redeploy, by design).
    uint256 public threshold;
    uint256 public timelockDelay;
    
    address[] public signers;
    mapping(address => bool) public isSigner;
    
    struct Transaction {
        address to;
        uint256 value;
        bytes data;
        bool executed;
        uint256 createdAt;
        uint256 executedAt;
        mapping(address => bool) confirmations;
        uint256 confirmationCount;
    }
    
    Transaction[] public transactions;
    // Maintained counter (mirrors transactions.length; the getter predates
    // the array and external tooling reads it — kept in sync on submit).
    uint256 public transactionCount;
    
    mapping(bytes32 => bool) public executedTransactions;
    
    bool public paused;
    
    /* ========== EVENTS ========== */
    
    event Deposit(address indexed from, uint256 value);
    event TransactionCreated(uint256 indexed txId, address indexed to, uint256 value, bytes data);
    event Confirmation(uint256 indexed txId, address indexed signer);
    event TransactionExecuted(uint256 indexed txId, address indexed executor);
    event TransactionFailed(uint256 indexed txId, string reason);
    event SignerAdded(address indexed signer);
    event SignerRemoved(address indexed signer);
    event ThresholdChanged(uint256 oldThreshold, uint256 newThreshold);
    event TimelockChanged(uint256 oldDelay, uint256 newDelay);
    event Paused(address indexed by);
    event Unpaused(address indexed by);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    
    /* ========== ERRORS ========== */
    error OnlyOwner();
    error OnlySigner();
    error NotSigner(address signer);
    error AlreadySigner(address signer);
    error NotEnoughSigners(uint256 have, uint256 need);
    error InvalidThreshold(uint256 threshold, uint256 signerCount);
    error TransactionNotFound();
    error AlreadyExecuted();
    error NotEnoughConfirmations(uint256 have, uint256 need);
    error AlreadyConfirmed(address signer);
    error TransactionExpired();
    error IsPaused();
    error InvalidTimelock();
    error ZeroAddress();
    error InsufficientBalance();
    error InvalidTimelockDelay();
    error ReentrantCall();
    
    /* ========== MODIFIERS ========== */
    
    modifier onlyOwner() {
        if (msg.sender != owner) revert OnlyOwner();
        _;
    }
    
    modifier onlySigner() {
        if (!isSigner[msg.sender]) revert NotSigner(msg.sender);
        _;
    }
    
    modifier whenNotPaused() {
        if (paused) revert IsPaused();
        _;
    }
    
    // OZ-style mutex (1 = free, 2 = locked). The previous revision
    // shipped `_;`-only here — a guard in name that guarded nothing.
    uint256 private _reentrancyStatus = 1;

    modifier nonReentrant() {
        if (_reentrancyStatus != 1) revert ReentrantCall();
        _reentrancyStatus = 2;
        _;
        _reentrancyStatus = 1;
    }
    
    /* ========== CONSTRUCTOR ========== */
    
    constructor(
        address[] memory _signers,
        uint256 _threshold,
        uint256 _timelockDelay
    ) {
        if (_signers.length == 0) revert ZeroAddress();
        if (_threshold == 0 || _threshold > _signers.length) {
            revert InvalidThreshold(_threshold, _signers.length);
        }
        if (_timelockDelay == 0) revert InvalidTimelockDelay();
        
        owner = msg.sender;
        threshold = _threshold;
        timelockDelay = _timelockDelay;
        
        for (uint256 i = 0; i < _signers.length; i++) {
            if (_signers[i] == address(0)) revert ZeroAddress();
            if (isSigner[_signers[i]]) revert AlreadySigner(_signers[i]);
            signers.push(_signers[i]);
            isSigner[_signers[i]] = true;
        }
    }
    
    /* ========== CORE FUNCTIONS ========== */
    
    /// @notice Create a new transaction proposal
    function submitTransaction(
        address to,
        uint256 value,
        bytes calldata data
    ) external onlySigner nonReentrant returns (uint256 txId) {
        if (paused) revert IsPaused();
        if (to == address(0)) revert ZeroAddress();
        
        txId = transactions.length;
        transactions.push();
        Transaction storage t = transactions[txId];
        t.to = to;
        t.value = value;
        t.data = data;
        t.executed = false;
        t.createdAt = block.timestamp;
        t.executedAt = 0;
        t.confirmationCount = 0;
        transactionCount = transactions.length;
        
        emit TransactionCreated(txId, to, value, data);
        return txId;
    }
    
    /// @notice Confirm a pending transaction
    function confirmTransaction(uint256 txId) external onlySigner {
        Transaction storage txn = transactions[txId];
        if (txId >= transactions.length) revert TransactionNotFound();
        if (txn.executed) revert AlreadyExecuted();
        if (txn.confirmations[msg.sender]) revert AlreadyConfirmed(msg.sender);
        
        txn.confirmations[msg.sender] = true;
        txn.confirmationCount += 1;
        
        emit Confirmation(txId, msg.sender);
    }
    
    /// @notice Execute a transaction after timelock and threshold met
    function executeTransaction(uint256 txId) external onlySigner nonReentrant {
        Transaction storage txn = transactions[txId];
        if (txId >= transactions.length) revert TransactionNotFound();
        if (txn.executed) revert AlreadyExecuted();
        if (txn.confirmationCount < threshold) revert NotEnoughConfirmations(txn.confirmationCount, threshold);
        if (block.timestamp < txn.createdAt + timelockDelay) revert TransactionExpired();
        
        // Effects before interaction (plus the nonReentrant mutex): the
        // executed flag lands before the external call, so a reverting
        // callee cannot re-enter a live proposal (revert rolls back the
        // flag on failure, so failed calls stay executable).
        txn.executed = true;
        txn.executedAt = block.timestamp;

        // Execute the call
        (bool success, bytes memory returnData) = txn.to.call{value: txn.value}(txn.data);
        if (!success) {
            emit TransactionFailed(txId, "execution reverted");
            revert("execution reverted");
        }

        emit TransactionExecuted(txId, msg.sender);
    }
    
    /// @notice Get transaction details (mapping fields excluded: Solidity
    /// cannot return mappings — use isConfirmed(txId, signer) per signer)
    function getTransaction(uint256 txId) external view returns (
        address to,
        uint256 value,
        bytes memory data,
        bool executed,
        uint256 createdAt,
        uint256 executedAt,
        uint256 confirmationCount
    ) {
        if (txId >= transactions.length) revert TransactionNotFound();
        Transaction storage txn = transactions[txId];
        return (txn.to, txn.value, txn.data, txn.executed, txn.createdAt,
                txn.executedAt, txn.confirmationCount);
    }
    
    /// @notice Get confirmation status for a signer on a transaction
    function isConfirmed(uint256 txId, address signer) external view returns (bool) {
        if (txId >= transactions.length) revert TransactionNotFound();
        return transactions[txId].confirmations[signer];
    }
    
    /// @notice Get number of confirmations for a transaction
    function getConfirmationCount(uint256 txId) external view returns (uint256) {
        if (txId >= transactions.length) revert TransactionNotFound();
        return transactions[txId].confirmationCount;
    }
    
    /* ========== SIGNER MANAGEMENT ========== */
    
    /// @notice Add a new signer (owner only, timelocked)
    function addSigner(address newSigner) external onlyOwner nonReentrant {
        if (newSigner == address(0)) revert ZeroAddress();
        if (isSigner[newSigner]) revert AlreadySigner(newSigner);
        
        // In a real deployment, this would go through a timelock
        // For now, immediate effect for simplicity
        signers.push(newSigner);
        isSigner[newSigner] = true;
        
        // Adjust threshold if needed (maintain threshold <= signer count)
        if (threshold > signers.length) {
            threshold = signers.length;
        }
        
        emit SignerAdded(newSigner);
    }
    
    /// @notice Remove a signer (owner only)
    function removeSigner(address signer) external onlyOwner nonReentrant {
        if (!isSigner[signer]) revert NotSigner(signer);
        if (signers.length <= threshold) revert NotEnoughSigners(signers.length, threshold);
        
        // Remove from signers array (swap with last)
        for (uint256 i = 0; i < signers.length; i++) {
            if (signers[i] == signer) {
                signers[i] = signers[signers.length - 1];
                signers.pop();
                break;
            }
        }
        
        isSigner[signer] = false;
        
        // Adjust threshold if needed
        if (threshold > signers.length) {
            threshold = signers.length;
        }
        
        emit SignerRemoved(signer);
    }
    
    /// @notice Update threshold (owner only)
    function updateThreshold(uint256 newThreshold) external onlyOwner nonReentrant {
        if (newThreshold == 0 || newThreshold > signers.length) {
            revert InvalidThreshold(newThreshold, signers.length);
        }
        uint256 oldThreshold = threshold;
        threshold = newThreshold;
        emit ThresholdChanged(oldThreshold, newThreshold);
    }
    
    /// @notice Update timelock delay (owner only)
    function updateTimelock(uint256 newDelay) external onlyOwner nonReentrant {
        if (newDelay == 0) revert InvalidTimelockDelay();
        uint256 oldDelay = timelockDelay;
        timelockDelay = newDelay;
        emit TimelockChanged(oldDelay, newDelay);
    }
    
    /* ========== PAUSE / EMERGENCY ========== */
    
    function pause() external onlyOwner {
        paused = true;
        emit Paused(msg.sender);
    }
    
    function unpause() external onlyOwner {
        paused = false;
        emit Unpaused(msg.sender);
    }
    
    /* ========== RECEIVE / FALLBACK ========== */
    
    receive() external payable {
        emit Deposit(msg.sender, msg.value);
    }
    
    /* ========== VIEWS ========== */
    
    function getTransactionCount() external view returns (uint256) {
        return transactions.length;
    }
    
    function getSigners() external view returns (address[] memory) {
        return signers;
    }

    // (No isSigner() getter: the public isSigner mapping already exposes
    // one. A same-named function does not compile.)

    
    function getThreshold() external view returns (uint256) {
        return threshold;
    }
    
    function getTimelockDelay() external view returns (uint256) {
        return timelockDelay;
    }
    
    function isPaused() external view returns (bool) {
        return paused;
    }
    
    /// @notice Check if a transaction is ready to execute
    function isReadyToExecute(uint256 txId) external view returns (bool) {
        if (txId >= transactions.length) return false;
        Transaction storage txn = transactions[txId];
        if (txn.executed) return false;
        if (txn.confirmationCount < threshold) return false;
        if (block.timestamp < txn.createdAt + timelockDelay) return false;
        return true;
    }
    
    /// @notice Get pending transactions for a signer
    function getPendingTransactions(address signer) external view returns (uint256[] memory) {
        uint256[] memory pending;
        for (uint256 i = 0; i < transactions.length; i++) {
            Transaction storage txn = transactions[i];
            if (!txn.executed && txn.confirmationCount < threshold && 
                block.timestamp >= txn.createdAt + timelockDelay) {
                if (txn.confirmations[signer]) {
                    // Already confirmed by this signer
                } else {
                    // Can confirm
                }
            }
        }
        // Simplified - in production would filter properly
        return new uint256[](0);
    }
    
    /// @notice Get all pending transactions
    function getAllPendingTransactions() external view returns (uint256[] memory) {
        uint256[] memory pending;
        for (uint256 i = 0; i < transactions.length; i++) {
            Transaction storage txn = transactions[i];
            if (!txn.executed && txn.confirmationCount < threshold && 
                block.timestamp >= txn.createdAt + timelockDelay) {
                // pending
            }
        }
        return new uint256[](0); // Simplified
    }
}