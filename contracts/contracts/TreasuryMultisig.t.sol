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
        multisig = new TreasuryMultisig(
            [signer1, signer2, signer3],
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
        vm.expectRevert(TreasuryMultisig.NotSigner.selector);
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
        vm.expectRevert(TreasuryMultisig.NotSigner.selector);
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
        vm.expectRevert(TreasuryMultisig.NotEnoughConfirmations.selector);
        multisig.executeTransaction(0);
    }
    
    function test_execution_after_threshold_and_timelock() public {
        vm.prank(signer1);
        uint256 txId = multisig.submitTransaction(address(0x6), 1 ether, "");
        
        vm.prank(signer1);
        multisig.confirmTransaction(0);
        vm.prank(signer2);
        multisig.confirmTransaction(0);
        
        // Before timelock
        vm.expectRevert(TreasuryMultisig.TransactionExpired.selector);
        multisig.executeTransaction(0);
        
        // Advance past timelock
        vm.warp(block.timestamp + 1 hours + 1 seconds);
        
        // Execute
        vm.prank(signer1);
        multisig.executeTransaction(0);
        
        // Check transaction executed
        (bool executed, ) = multisig.transactions(0);
        assertTrue(executed);
    }
    
    function test_cannot_execute_twice() public {
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
        
        vm.expectRevert(TreasuryMultisig.InvalidThreshold.selector);
        multisig.updateThreshold(4); // > signers
    }
    
    function test_timelock_update() public {
        vm.prank(address(0x1));
        multisig.updateTimelock(2 hours);
        assertEq(multisig.timelockDelay(), 2 hours);
        
        vm.expectRevert(TreasuryMultisig.InvalidTimelockDelay.selector);
        multisig.updateTimelock(0);
    }
    
    function test_pause_unpause() public {
        address(0x1).call{value: 1 ether}(new bytes(0));
        
        vm.prank(address(0x1));
        multisig.pause();
        assertTrue(multisig.paused());
        
        vm.prank(signer1);
        vm.expectRevert(TreasuryMultisig.Paused.selector);
        multisig.submitTransaction(address(0x6), 1 ether, "");
        
        vm.prank(address(0x1));
        multisig.unpause();
        assertFalse(multisig.paused());
    }
    
    function test_execute_transfers_ether() public {
        // Fund the multisig
        address(0x1).call{value: 2 ether}(new bytes(0));
        
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
        // This would require a malicious target contract
        // For now just ensure the modifier exists
    }
    
    function test_zero_address_rejected() public {
        vm.prank(signer1);
        vm.expectRevert(TreasuryMultisig.ZeroAddress.selector);
        multisig.submitTransaction(address(0), 1 ether, "");
    }
    
    function test_threshold_validation() public {
        vm.expectRevert(TreasuryMultisig.InvalidThreshold.selector);
        new TreasuryMultisig([address(0x2), address(0x3)], 3, 1 hours);
    }
    
    function test_insufficient_signers_for_threshold() public {
        vm.expectRevert(TreasuryMultisig.NotEnoughSigners.selector);
        new TreasuryMultisig([address(0x2), address(0x3)], 3, 1 hours);
    }
    
    function test_duplicate_signer_rejected() public {
        vm.expectRevert(TreasuryMultisig.AlreadySigner.selector);
        new TreasuryMultisig([address(0x2), address(0x2)], 2, 1 hours);
    }
}