// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../contracts/TreasuryMultisig.sol";

contract TreasuryMultisigTest is Test {
    TreasuryMultisig multisig;
    address owner = address(0x1);
    address signer1 = address(0x2);
    address signer2 = address(0x3);
    address signer3 = address(0x4);
    address nonSigner = address(0x5);
    address target = address(0x6);
    
    function setUp() public {
        vm.prank(address(0x1));
        address[] memory initial = new address[](3);
        initial[0] = signer1;
        initial[1] = signer2;
        initial[2] = signer3;
        multisig = new TreasuryMultisig(
            initial,
            2, // 2-of-3 threshold
            1 hours
        );
    }
    
    function test_deployment_sets_correct_state() public {
        assertEq(multisig.owner(), address(0x1));
        assertEq(multisig.threshold(), 2);
        assertEq(multisig.timelockDelay(), 1 hours);
        assertEq(multisig.signers(0), address(0x2));
        assertEq(multisig.signers(1), address(0x3));
        assertEq(multisig.signers(2), address(0x4));
        assertTrue(multisig.isSigner(address(0x2)));
        assertTrue(multisig.isSigner(address(0x3)));
        assertTrue(multisig.isSigner(address(0x4)));
        assertFalse(multisig.isSigner(address(0x5)));
    }
    
    function test_submit_transaction_requires_signer() public {
        vm.prank(nonSigner);
        vm.expectRevert(abi.encodeWithSelector(
            TreasuryMultisig.NotSigner.selector, nonSigner));
        multisig.submitTransaction(address(0x6), 1 ether, "");
    }
    
    function test_submit_transaction_creates_proposal() public {
        vm.prank(signer1);
        uint256 txId = multisig.submitTransaction(address(0x6), 1 ether, "");
        
        assertEq(multisig.transactionCount(), 1);
        
        (address to, uint256 value, bytes memory data, bool executed, 
         uint256 createdAt, uint256 executedAt, uint256 confirmationCount) = 
            multisig.getTransaction(0);
        
        assertEq(to, address(0x6));
        assertEq(value, 1 ether);
        assertFalse(executed);
        assertEq(confirmationCount, 0);
    }
    
    function test_confirmation_requires_signer() public {
        vm.prank(signer1);
        uint256 txId = multisig.submitTransaction(address(0x6), 1 ether, "");
        
        vm.prank(nonSigner);
        vm.expectRevert(abi.encodeWithSelector(
            TreasuryMultisig.NotSigner.selector, nonSigner));
        multisig.confirmTransaction(0);
    }
    
    function test_confirmation_increments_count() public {
        vm.prank(signer1);
        uint256 txId = multisig.submitTransaction(address(0x6), 1 ether, "");
        
        vm.prank(signer2);
        multisig.confirmTransaction(0);
        
        assertEq(multisig.getConfirmationCount(0), 1);
    }
    
    function test_execution_requires_threshold() public {
        vm.prank(signer1);
        uint256 txId = multisig.submitTransaction(address(0x6), 1 ether, "");
        
        // Only 1 confirmation, threshold is 2
        vm.prank(signer1);
        multisig.confirmTransaction(0);
        vm.prank(signer1);
        vm.expectRevert(abi.encodeWithSelector(
            TreasuryMultisig.NotEnoughConfirmations.selector, 1, 2));
        multisig.executeTransaction(0);
    }
    
    function test_execution_after_threshold_and_timelock() public {
        vm.deal(address(multisig), 2 ether);
        vm.prank(signer1);
        uint256 txId = multisig.submitTransaction(address(0x6), 1 ether, "");
        
        vm.prank(signer1);
        multisig.confirmTransaction(0);
        vm.prank(signer2);
        multisig.confirmTransaction(0);
        
        // Before timelock (the contract calls early-execution
        // TransactionExpired — misleading name, correct gate)
        vm.prank(signer1);
        vm.expectRevert(TreasuryMultisig.TransactionExpired.selector);
        multisig.executeTransaction(0);
        
        // Advance past timelock
        vm.warp(block.timestamp + 1 hours + 1 seconds);
        
        // Execute
        vm.prank(signer1);
        multisig.executeTransaction(0);
        
        // Check transaction executed
        (, , , bool executed, , , ) = multisig.getTransaction(0);
        assertTrue(executed);
    }
    
    function test_cannot_execute_twice() public {
        vm.deal(address(multisig), 2 ether);
        vm.prank(signer1);
        uint256 txId = multisig.submitTransaction(address(0x6), 1 ether, "");
        vm.prank(signer1);
        multisig.confirmTransaction(0);
        vm.prank(signer2);
        multisig.confirmTransaction(0);
        
        vm.warp(block.timestamp + 1 hours + 1 seconds);
        vm.prank(signer1);
        multisig.executeTransaction(0);
        
        vm.prank(signer1);
        vm.expectRevert(TreasuryMultisig.AlreadyExecuted.selector);
        multisig.executeTransaction(0);
    }
    
    function test_signer_management() public {
        // Add signer
        vm.prank(address(0x1));
        multisig.addSigner(address(0x7));
        assertTrue(multisig.isSigner(address(0x7)));
        
        // Remove signer
        vm.prank(address(0x1));
        multisig.removeSigner(address(0x2));
        assertFalse(multisig.isSigner(address(0x2)));
        
        // Threshold adjusts
        assertEq(multisig.threshold(), 2); // was 2-of-3, now 2-of-2
    }
    
    function test_threshold_update() public {
        vm.prank(address(0x1));
        multisig.updateThreshold(3);
        assertEq(multisig.threshold(), 3);

        vm.prank(address(0x1));
        vm.expectRevert(abi.encodeWithSelector(
            TreasuryMultisig.InvalidThreshold.selector, 4, 3));
        multisig.updateThreshold(4); // > signers
    }

    function test_timelock_update() public {
        vm.prank(address(0x1));
        multisig.updateTimelock(2 hours);
        assertEq(multisig.timelockDelay(), 2 hours);

        vm.prank(address(0x1));
        vm.expectRevert(TreasuryMultisig.InvalidTimelockDelay.selector);
        multisig.updateTimelock(0);
    }
    
    function test_pause_unpause() public {
        vm.deal(address(0x1), 10 ether);
        (bool ok, ) = address(0x1).call{value: 1 ether}(new bytes(0));
        assertTrue(ok);
        
        vm.prank(address(0x1));
        multisig.pause();
        assertTrue(multisig.paused());
        
        vm.prank(signer1);
        vm.expectRevert(TreasuryMultisig.IsPaused.selector);
        multisig.submitTransaction(address(0x6), 1 ether, "");
        
        vm.prank(address(0x1));
        multisig.unpause();
        assertFalse(multisig.paused());
    }
    
    function test_execute_transfers_ether() public {
        // Fund the multisig
        vm.deal(address(multisig), 2 ether);

        vm.prank(signer1);
        uint256 txId = multisig.submitTransaction(payable(address(0x6)), 1 ether, "");
        vm.prank(signer1);
        multisig.confirmTransaction(0);
        vm.prank(signer2);
        multisig.confirmTransaction(0);
        
        vm.warp(block.timestamp + 1 hours + 1 seconds);
        
        uint256 before = address(0x6).balance;
        vm.prank(signer1);
        multisig.executeTransaction(0);
        
        assertEq(address(0x6).balance, before + 1 ether);
    }
    
    function test_reentrancy_protection() public {
        // Genuine attack drill: the reentrant caller must itself be a
        // signer (onlySigner gates entry), so the attacker joins a fresh
        // 2-of-3 set and re-enters the SAME proposal on payment receipt.
        // The mutex (plus executed-before-call) must make the inner call
        // revert; the outer execution then fails closed with funds in place.
        ReenterAttacker attacker = new ReenterAttacker(multisig);
        TreasuryMultisig guarded = new TreasuryMultisig(
            _asArray(signer1, signer2, address(attacker)), 2, 1 hours);
        attacker.setTarget(guarded);
        vm.deal(address(guarded), 2 ether);

        vm.prank(signer1);
        uint256 txId = guarded.submitTransaction(
            address(attacker), 1 ether, "");
        vm.prank(signer1);
        guarded.confirmTransaction(txId);
        vm.prank(signer2);
        guarded.confirmTransaction(txId);

        vm.warp(block.timestamp + 1 hours + 1 seconds);
        vm.prank(signer1);
        vm.expectRevert("execution reverted");
        guarded.executeTransaction(txId);

        // Funds never left, proposal never marked executed.
        assertEq(address(guarded).balance, 2 ether);
        (, , , bool executed, , , ) = guarded.getTransaction(txId);
        assertFalse(executed);
    }

    function _asArray(address a, address b, address c)
        internal pure returns (address[] memory)
    {
        address[] memory arr = new address[](3);
        arr[0] = a;
        arr[1] = b;
        arr[2] = c;
        return arr;
    }
    
    function test_zero_address_rejected() public {
        vm.prank(signer1);
        vm.expectRevert(TreasuryMultisig.ZeroAddress.selector);
        multisig.submitTransaction(address(0), 1 ether, "");
    }
    
    function test_threshold_validation() public {
        address[] memory two = new address[](2);
        two[0] = address(0x2);
        two[1] = address(0x3);
        vm.expectRevert(abi.encodeWithSelector(
            TreasuryMultisig.InvalidThreshold.selector, 3, 2));
        new TreasuryMultisig(two, 3, 1 hours);
    }

    function test_insufficient_signers_for_threshold() public {
        // 2-of-3: first removal succeeds (3 > 2), second would leave
        // 1-of-2 and the guard refuses with NotEnoughSigners(2, 2).
        vm.prank(address(0x1));
        multisig.removeSigner(signer3);
        vm.prank(address(0x1));
        vm.expectRevert(abi.encodeWithSelector(
            TreasuryMultisig.NotEnoughSigners.selector, 2, 2));
        multisig.removeSigner(signer2);
    }

    function test_duplicate_signer_rejected() public {
        address[] memory dup = new address[](2);
        dup[0] = address(0x2);
        dup[1] = address(0x2);
        vm.expectRevert(abi.encodeWithSelector(
            TreasuryMultisig.AlreadySigner.selector, address(0x2)));
        new TreasuryMultisig(dup, 2, 1 hours);
    }
}


/// @notice Malicious target: re-enters the multisig on payment receipt.
/// Must be a signer itself, else onlySigner (not the mutex) stops it —
/// which is exactly why the drill deploys a guarded set including it.
contract ReenterAttacker {
    TreasuryMultisig public target;

    constructor(TreasuryMultisig _target) {
        target = _target;
    }

    function setTarget(TreasuryMultisig _target) external {
        target = _target;
    }

    receive() external payable {
        // Re-enter the same proposal mid-execution. The mutex must revert
        // this (executed-flag is also already set: defense in depth).
        target.executeTransaction(0);
    }
}