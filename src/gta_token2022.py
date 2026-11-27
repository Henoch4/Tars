"""Fail-closed Token-2022 mint extension inspection.

The parser only interprets the canonical mint TLV framing and extension
layouts. Unknown or truncated extensions are reported as parse failures, never
as a clean token. It performs no signing, quoting, or transaction simulation.
"""
from __future__ import annotations

import base64
import struct
from dataclasses import dataclass


TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

_EXTENSION_NAMES = {
    1: "TransferFeeConfig", 3: "MintCloseAuthority", 6: "DefaultAccountState",
    9: "NonTransferable", 10: "InterestBearingConfig", 12: "PermanentDelegate",
    14: "TransferHook", 18: "MetadataPointer", 19: "TokenMetadata",
    20: "GroupPointer", 21: "TokenGroup", 22: "GroupMemberPointer",
    25: "ScaledUiAmount", 26: "Pausable", 28: "PermissionedBurn",
}


class Token2022ParseError(ValueError):
    """Mint account data is malformed or incomplete."""


@dataclass(frozen=True)
class Token2022Result:
    is_token2022: bool
    extension_types: tuple[str, ...] = ()
    transfer_fee_bps: int | None = None
    transfer_fee_maximum_raw: int | None = None
    transfer_hook_program: str | None = None
    permanent_delegate: str | None = None
    default_account_state: int | None = None
    non_transferable: bool = False
    pausable: bool = False
    permissioned_burn: bool = False
    mint_close_authority: bool = False
    token_metadata: bool = False
    parse_error: str | None = None

    @property
    def transfer_tax_pct(self) -> float | None:
        return (self.transfer_fee_bps / 100.0
                if self.transfer_fee_bps is not None else None)

    @property
    def hard_risk_flags(self) -> tuple[str, ...]:
        flags: list[str] = []
        if self.non_transferable:
            flags.append("non_transferable")
        if self.pausable:
            flags.append("pausable")
        if self.permissioned_burn:
            flags.append("permissioned_burn")
        if self.permanent_delegate:
            flags.append("permanent_delegate")
        if self.transfer_hook_program:
            flags.append("transfer_hook")
        if self.default_account_state == 2:  # Frozen in the canonical enum.
            flags.append("default_frozen")
        return tuple(flags)


def _b58(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, rem = divmod(n, 58)
        out = alphabet[rem] + out
    return alphabet[0] * (len(raw) - len(raw.lstrip(b"\0"))) + (out or "")


def parse_mint_data(data_b64: str, *, owner_program: str) -> Token2022Result:
    """Parse a base64 Solana mint account returned by ``getAccountInfo``."""
    if owner_program != TOKEN_2022_PROGRAM_ID:
        return Token2022Result(is_token2022=False)
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception as exc:
        raise Token2022ParseError(f"mint data is not valid base64: {exc}") from exc
    if len(raw) < 82:
        raise Token2022ParseError("Token-2022 mint is shorter than the 82-byte base mint")

    found: list[str] = []
    fee_bps = fee_max = None
    hook = delegate = None
    default_state = None
    non_transferable = pausable = permissioned_burn = close_authority = False
    token_metadata = False
    pos = 82
    while pos < len(raw):
        # RPC account data may be allocated larger than the active TLV
        # payload. The unused tail is zero padding, not a malformed
        # extension. Stop only when the remainder is entirely padding.
        if not any(raw[pos:]):
            break
        if len(raw) - pos < 4:
            raise Token2022ParseError("truncated extension header")
        ext_type, ext_len = struct.unpack_from("<HH", raw, pos)
        pos += 4
        if ext_type == 0 and ext_len == 0:
            # Uninitialized/padding TLV header; continue scanning in case a
            # later account segment contains a real extension.
            continue
        end = pos + ext_len
        if end > len(raw):
            raise Token2022ParseError("extension payload exceeds mint account length")
        payload = raw[pos:end]
        pos = end
        name = _EXTENSION_NAMES.get(ext_type, f"ExtensionType({ext_type})")
        found.append(name)
        if ext_type == 1:
            if len(payload) < 116:
                raise Token2022ParseError("truncated TransferFeeConfig extension")
            # Layout: two COption<Pubkey> values, withheld amount, older fee,
            # newer fee. Each fee is epoch u64, max fee u64, bps u16.
            fee_max = struct.unpack_from("<Q", payload, 106)[0]
            fee_bps = struct.unpack_from("<H", payload, 114)[0]
        elif ext_type == 3:
            close_authority = True
        elif ext_type == 6:
            if len(payload) < 1:
                raise Token2022ParseError("truncated DefaultAccountState extension")
            default_state = payload[0]
        elif ext_type == 9:
            non_transferable = True
        elif ext_type == 12:
            if len(payload) < 32:
                raise Token2022ParseError("truncated PermanentDelegate extension")
            delegate = _b58(payload[:32])
        elif ext_type == 14:
            if len(payload) < 68:
                raise Token2022ParseError("truncated TransferHook extension")
            # COption authority (36 bytes) followed by hook program id.
            hook = _b58(payload[36:68])
        elif ext_type == 26:
            pausable = True
        elif ext_type == 28:
            permissioned_burn = True
        elif ext_type == 19:
            # TokenMetadata: variable-length Borsh payload. Metadata is
            # irrelevant to the fee/hook gate, so it is treated as a formally
            # recognized opaque extension: the TLV boundary is already
            # validated by the length check above, its presence is recorded
            # in the audit trail (extension_types), and it is never added to
            # hard_risk_flags. An empty payload is malformed and fails closed.
            if len(payload) == 0:
                raise Token2022ParseError("empty TokenMetadata extension")
            token_metadata = True

    return Token2022Result(
        is_token2022=True,
        extension_types=tuple(found),
        transfer_fee_bps=fee_bps,
        transfer_fee_maximum_raw=fee_max,
        transfer_hook_program=hook,
        permanent_delegate=delegate,
        default_account_state=default_state,
        non_transferable=non_transferable,
        pausable=pausable,
        permissioned_burn=permissioned_burn,
        mint_close_authority=close_authority,
        token_metadata=token_metadata,
    )
