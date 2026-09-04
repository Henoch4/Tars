"""S8 regression: one canonical decision digest for signing AND verification.

Before the fix, audit_logger._compute_payload_hash (abi.encodePacked layout,
what TradeAuditTrail.sol recomputes) and governance.compute_payload_hash
(JSON keccak) produced DIFFERENT digests for the same decision. The signing
path was correct per test_signature_roundtrip.py, but VerifyClient verified
signatures against a digest the contract never computed — verification that
passes/fails on the wrong bytes (S8: "hash verification is not validation").

These tests pin: both producers emit the identical digest (single leg and
package leg), chain-sourced bytes32 inputs reproduce it, malformed inputs
raise instead of packing garbage, and verify_decision_integrity actually
verifies (it was a `return True` stub).
"""
import pytest
from eth_account import Account
from web3 import Web3

from src.audit_logger import DecisionPayload, OnchainLogger
from src.governance import (
    GenericDecisionPayload,
    canonical_decision_hash,
    compute_payload_hash,
    verify_decision_integrity,
)


TEST_PRIVATE_KEY = "0x" + "22" * 32  # deterministic test key, not fund-bearing


def _make_test_logger():
    logger = OnchainLogger.__new__(OnchainLogger)
    logger.w3 = Web3()
    logger.private_key = TEST_PRIVATE_KEY
    logger.account = Account.from_key(TEST_PRIVATE_KEY)
    return logger


def _signing_payload(logger, package_id=None):
    return DecisionPayload(
        decision_id="canonical-test-001",
        agent_address=logger.account.address,
        asset="BTC-USDT-SWAP",
        signal="LONG",
        strategy="mean_reversion",
        confidence_bps=8500,
        entry_price=50000.0,
        size_usd=1000.0,
        risk_params_hash="0x" + "33" * 32,
        timestamp=1700000000,
        package_id=package_id,
    )


def _generic_payload(logger, package_id=None):
    return GenericDecisionPayload(
        decision_id="canonical-test-001",
        agent_id=logger.account.address,
        action_type="trade",
        action_params={
            "asset": "BTC-USDT-SWAP",
            "signal": "LONG",
            "entry_price": 50000.0,
            "size_usd": 1000.0,
        },
        confidence_bps=8500,
        rationale="mean_reversion",
        risk_context_hash="0x" + "33" * 32,
        timestamp=1700000000,
        metadata={"package_id": package_id} if package_id else {},
    )


class TestCanonicalHashAgreement:
    def test_single_leg_hashes_agree(self):
        """The S8 core: signing digest == verification digest, single leg."""
        logger = _make_test_logger()
        signing = logger._compute_payload_hash(_signing_payload(logger))
        verifying = bytes.fromhex(compute_payload_hash(_generic_payload(logger)))
        assert signing == verifying

    def test_package_leg_hashes_agree(self):
        """Same agreement with a package_id set (multi-leg path)."""
        logger = _make_test_logger()
        signing = logger._compute_payload_hash(
            _signing_payload(logger, package_id="pkg-canonical-001")
        )
        verifying = bytes.fromhex(
            compute_payload_hash(_generic_payload(logger, package_id="pkg-canonical-001"))
        )
        assert signing == verifying

    def test_package_and_single_leg_differ(self):
        """package_id participates: same decision with/without package differs."""
        logger = _make_test_logger()
        assert logger._compute_payload_hash(_signing_payload(logger)) != (
            logger._compute_payload_hash(_signing_payload(logger, package_id="pkg-x"))
        )

    def test_chain_digest_inputs_reproduce_signing_hash(self):
        """VerifyClient receives bytes32 digests from chain events, not the
        preimage strings. Feeding those digests must reproduce the exact
        signing digest — this is the whole point of the canonical scheme."""
        logger = _make_test_logger()
        signing = logger._compute_payload_hash(
            _signing_payload(logger, package_id="pkg-chain-001")
        )
        chain_style = GenericDecisionPayload(
            decision_id=Web3.keccak(text="canonical-test-001"),
            agent_id=logger.account.address,
            action_type="trade",
            action_params={
                "asset": "BTC-USDT-SWAP",
                "signal": "LONG",
                "entry_price": 50000.0,
                "size_usd": 1000.0,
            },
            confidence_bps=8500,
            rationale="mean_reversion",
            risk_context_hash="0x" + "33" * 32,
            timestamp=1700000000,
            metadata={"package_id": Web3.keccak(text="pkg-chain-001")},
        )
        assert bytes.fromhex(compute_payload_hash(chain_style)) == signing

    def test_to_generic_roundtrip_matches(self):
        """TradingDecisionPayload.to_generic() feeds compute_payload_hash
        and must match the signing digest — the Product A bridge path."""
        from src.governance import TradingDecisionPayload

        logger = _make_test_logger()
        trading = TradingDecisionPayload(
            decision_id="canonical-test-001",
            agent_address=logger.account.address,
            asset="BTC-USDT-SWAP",
            signal="LONG",
            strategy="mean_reversion",
            confidence_bps=8500,
            entry_price=50000.0,
            size_usd=1000.0,
            risk_params_hash="0x" + "33" * 32,
            timestamp=1700000000,
        )
        assert bytes.fromhex(compute_payload_hash(trading.to_generic())) == (
            logger._compute_payload_hash(_signing_payload(logger))
        )


class TestCanonicalHashFailClosed:
    def test_short_risk_hash_raises(self):
        """A truncated risk hash previously packed short and silently
        produced a wrong-length digest. Now it raises."""
        logger = _make_test_logger()
        with pytest.raises(ValueError):
            canonical_decision_hash(
                "id", None, logger.account.address, "BTC-USDT-SWAP",
                "LONG", "mean_reversion", 8500, 50000.0, 1000.0, "0x1234",
            )

    def test_empty_risk_hash_raises(self):
        logger = _make_test_logger()
        with pytest.raises(ValueError):
            canonical_decision_hash(
                "id", None, logger.account.address, "BTC-USDT-SWAP",
                "LONG", "mean_reversion", 8500, 50000.0, 1000.0, "",
            )

    def test_malformed_address_raises(self):
        with pytest.raises(ValueError):
            canonical_decision_hash(
                "id", None, "not-an-address", "BTC-USDT-SWAP",
                "LONG", "mean_reversion", 8500, 50000.0, 1000.0,
                "0x" + "33" * 32,
            )

    def test_short_address_raises(self):
        with pytest.raises(ValueError):
            canonical_decision_hash(
                "id", None, "0x1234", "BTC-USDT-SWAP",
                "LONG", "mean_reversion", 8500, 50000.0, 1000.0,
                "0x" + "33" * 32,
            )


class TestVerifyDecisionIntegrity:
    def test_valid_signature_verifies(self):
        """The old stub returned True for everything; a real signature
        from the agent key must verify True."""
        logger = _make_test_logger()
        payload = _generic_payload(logger)
        signature = logger._sign_payload(_signing_payload(logger)).hex()
        assert verify_decision_integrity(payload, signature, logger.account.address) is True

    def test_wrong_agent_fails(self):
        """A valid signature attributed to the wrong agent must fail —
        the stub returned True here too."""
        logger = _make_test_logger()
        payload = _generic_payload(logger)
        signature = logger._sign_payload(_signing_payload(logger)).hex()
        other = Account.create().address
        assert verify_decision_integrity(payload, signature, other) is False

    def test_garbage_signature_fails_closed(self):
        logger = _make_test_logger()
        assert verify_decision_integrity(
            _generic_payload(logger), "0xdeadbeef", logger.account.address
        ) is False
