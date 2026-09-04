"""Pluggable attestation signers (D10 + ika CAPTURE-1).

Signing is decoupled from custody: the audit trail signs EIP-191 digests
through a SignerBackend, so custodial (env key) → connect-wallet session
is a backend swap, not a rewrite. The digest itself always comes from the
single canonical function (governance.canonical_decision_hash, S8) — the
signer never invents bytes to sign, which is the byte-equality discipline
ika's fail-closed rule demands, achieved structurally rather than by
re-checking.

Backends:
- EnvKeySigner: current custodial behavior (AGENT_WALLET_PRIVATE_KEY).
- DelegatedSigner: ephemeral session key with a bounded lifetime
  (ika CAPTURE-3 shape: session id + deadline, authority cannot outlive
  the session). Fully functional for attestation signing TODAY; the
  *authorization* half (an on-chain delegation registry so the contract
  accepts session-key signatures, plus treasury multisig) is the gated
  future step — until then, live broadcast still needs the operator key.

Neither backend touches transaction broadcast: that path keeps using the
operator key explicitly (see OnchainLogger._send_transaction), so a
delegated session can never silently gain spend authority.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from eth_account import Account
from eth_account.messages import encode_defunct


class SignerError(RuntimeError):
    """Typed signer failure (closed set via subclasses/messages)."""


class SessionExpiredError(SignerError):
    """A signing request arrived after the session deadline — refused."""


class SignerBackend(ABC):
    """EIP-191 message signer for audit attestations."""

    @property
    @abstractmethod
    def address(self) -> str:
        """The 0x address signatures from this backend recover to."""
        ...

    @abstractmethod
    def sign_message_digest(self, digest: bytes) -> bytes:
        """personal_sign a 32-byte digest. Must be exactly 32 bytes."""
        ...


def _check_digest(digest: bytes) -> None:
    if not isinstance(digest, (bytes, bytearray)) or len(digest) != 32:
        raise SignerError(
            f"refusing to sign: digest must be 32 bytes, got "
            f"{type(digest).__name__} len {len(digest) if hasattr(digest, '__len__') else '?'}"
        )


class EnvKeySigner(SignerBackend):
    """Custodial backend: signs with a held private key (current behavior)."""

    def __init__(self, private_key: str):
        self._account = Account.from_key(private_key)

    @property
    def address(self) -> str:
        return self._account.address

    def sign_message_digest(self, digest: bytes) -> bytes:
        _check_digest(digest)
        return self._account.sign_message(
            encode_defunct(primitive=bytes(digest))).signature


class DelegatedSigner(SignerBackend):
    """Ephemeral session backend: fresh key, bounded lifetime.

    Models the connect-wallet session shape (session id + deadline) with a
    throwaway key, so session lifecycle, expiry enforcement, and backend
    swapping are all exercised before any real delegation exists. Expired
    sessions REFUSE to sign — authority never outlives the session.
    """

    def __init__(self, session_id: str, ttl_seconds: float = 3600.0):
        if not session_id:
            raise SignerError("refusing to start: empty session_id")
        if ttl_seconds <= 0:
            raise SignerError("refusing to start: non-positive ttl")
        self.session_id = session_id
        self.expires_at = time.time() + ttl_seconds
        self._account = Account.create()

    @property
    def address(self) -> str:
        return self._account.address

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def sign_message_digest(self, digest: bytes) -> bytes:
        _check_digest(digest)
        if self.expired:
            raise SessionExpiredError(
                f"refusing to sign: session {self.session_id!r} expired")
        return self._account.sign_message(
            encode_defunct(primitive=bytes(digest))).signature
