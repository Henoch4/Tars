"""Signer backend regression (D10 + ika CAPTURE-1): signing decoupled
from custody. Custodial -> session is a backend swap; the digest signed is
always the canonical one; expired sessions refuse.
"""
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from src.audit_logger import DecisionPayload, OnchainLogger
from src.signer import (
    DelegatedSigner,
    EnvKeySigner,
    SessionExpiredError,
    SignerError,
)

TEST_PRIVATE_KEY = "0x" + "33" * 32


def _make_test_logger(**overrides):
    logger = OnchainLogger.__new__(OnchainLogger)
    logger.w3 = Web3()
    logger.private_key = TEST_PRIVATE_KEY
    logger.account = Account.from_key(TEST_PRIVATE_KEY)
    logger.signer = overrides.get("signer") or EnvKeySigner(TEST_PRIVATE_KEY)
    logger.agent_address = logger.signer.address
    return logger


def _payload(logger):
    return DecisionPayload(
        decision_id="signer-test-001",
        agent_address=logger.agent_address,
        asset="BTC-USDT-SWAP",
        signal="LONG",
        strategy="mean_reversion",
        confidence_bps=8500,
        entry_price=50000.0,
        size_usd=1000.0,
        risk_params_hash="0x" + "44" * 32,
        timestamp=1700000000,
    )


def _recover(digest: bytes, sig: bytes) -> str:
    return Account.recover_message(
        encode_defunct(primitive=digest), signature=sig).lower()


class TestEnvKeySigner:
    def test_address_matches_key(self):
        signer = EnvKeySigner(TEST_PRIVATE_KEY)
        assert signer.address.lower() == Account.from_key(TEST_PRIVATE_KEY).address.lower()

    def test_roundtrip_recovers(self):
        signer = EnvKeySigner(TEST_PRIVATE_KEY)
        digest = Web3.keccak(text="hello")
        sig = signer.sign_message_digest(digest)
        assert _recover(digest, sig) == signer.address.lower()

    def test_rejects_non_32_bytes(self):
        signer = EnvKeySigner(TEST_PRIVATE_KEY)
        with pytest.raises(SignerError):
            signer.sign_message_digest(b"short")

    def test_matches_legacy_direct_path(self):
        """Backend output is byte-identical to the pre-interface inline
        signing it replaced — the swap changed plumbing, not signatures."""
        signer = EnvKeySigner(TEST_PRIVATE_KEY)
        digest = Web3.keccak(text="compat")
        legacy = Account.from_key(TEST_PRIVATE_KEY).sign_message(
            encode_defunct(primitive=digest)).signature
        assert signer.sign_message_digest(digest) == legacy


class TestLoggerUsesBackend:
    def test_injected_backend_signs(self):
        class _Canned:
            address = "0x" + "aa" * 20
            seen: list = []

            def sign_message_digest(self, digest: bytes) -> bytes:
                self.seen.append(digest)
                return b"\x01" * 65

        canned = _Canned()
        logger = _make_test_logger(signer=canned)
        sig = logger._sign_payload(_payload(logger))
        assert sig == b"\x01" * 65
        # The backend received the canonical digest, not invented bytes.
        assert canned.seen == [logger._compute_payload_hash(_payload(logger))]

    def test_default_backend_is_custodial(self):
        logger = _make_test_logger()
        assert isinstance(logger.signer, EnvKeySigner)
        assert logger.agent_address.lower() == logger.signer.address.lower()

    def test_backend_signature_verifies_end_to_end(self):
        logger = _make_test_logger()
        payload = _payload(logger)
        sig = logger._sign_payload(payload)
        assert _recover(logger._compute_payload_hash(payload), sig) == (
            logger.agent_address.lower())


class TestDelegatedSigner:
    def test_fresh_session_signs(self):
        signer = DelegatedSigner("sess-1", ttl_seconds=3600.0)
        digest = Web3.keccak(text="attest")
        assert _recover(digest, signer.sign_message_digest(digest)) == (
            signer.address.lower())
        assert signer.expired is False

    def test_expired_session_refuses(self):
        signer = DelegatedSigner("sess-2", ttl_seconds=3600.0)
        signer.expires_at = 0.0  # force expiry without sleeping
        assert signer.expired is True
        with pytest.raises(SessionExpiredError):
            signer.sign_message_digest(Web3.keccak(text="attest"))

    def test_sessions_are_distinct_identities(self):
        a = DelegatedSigner("a")
        b = DelegatedSigner("b")
        assert a.address != b.address

    def test_empty_session_or_ttl_rejected(self):
        with pytest.raises(SignerError):
            DelegatedSigner("")
        with pytest.raises(SignerError):
            DelegatedSigner("x", ttl_seconds=0)
