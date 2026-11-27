import base64
import struct

import pytest

from src.gta_token2022 import (
    TOKEN_2022_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
    Token2022ParseError,
    parse_mint_data,
)


def _mint_with_extensions(*extensions):
    raw = bytearray(82)
    for ext_type, payload in extensions:
        raw.extend(struct.pack("<HH", ext_type, len(payload)))
        raw.extend(payload)
    return base64.b64encode(bytes(raw)).decode()


def test_classic_spl_has_no_token2022_extensions():
    result = parse_mint_data(base64.b64encode(bytes(82)).decode(),
                             owner_program=TOKEN_PROGRAM_ID)
    assert result.is_token2022 is False


def test_transfer_fee_and_risky_extensions_are_decoded():
    fee = bytearray(116)
    struct.pack_into("<Q", fee, 106, 1234)
    struct.pack_into("<H", fee, 114, 250)
    hook = bytes(68)
    result = parse_mint_data(
        _mint_with_extensions((1, bytes(fee)), (9, b""), (14, hook), (26, b"")),
        owner_program=TOKEN_2022_PROGRAM_ID,
    )
    assert result.transfer_fee_bps == 250
    assert result.transfer_tax_pct == 2.5
    assert result.transfer_fee_maximum_raw == 1234
    assert result.non_transferable is True
    assert result.pausable is True
    assert result.transfer_hook_program is not None
    assert "non_transferable" in result.hard_risk_flags


def test_truncated_extension_fails_closed():
    with pytest.raises(Token2022ParseError):
        parse_mint_data(_mint_with_extensions((1, b"short")),
                        owner_program=TOKEN_2022_PROGRAM_ID)


def test_zero_padded_account_tail_is_not_a_fake_extension():
    raw = base64.b64decode(_mint_with_extensions((9, b""))) + bytes(79)
    result = parse_mint_data(base64.b64encode(raw).decode(),
                             owner_program=TOKEN_2022_PROGRAM_ID)
    assert result.non_transferable is True


# Minimal valid TokenMetadata Borsh payload:
# update_authority=None(1) + mint(32) + name=None(1) + symbol=None(1)
# + uri=None(1) + additional_metadata=None(1) = 37 bytes.
_METADATA_PAYLOAD = bytes(37)


def test_metadata_only_extension_is_recorded_and_irrelevant_to_gate():
    result = parse_mint_data(
        _mint_with_extensions((19, _METADATA_PAYLOAD)),
        owner_program=TOKEN_2022_PROGRAM_ID,
    )
    assert result.is_token2022 is True
    assert result.token_metadata is True
    assert "TokenMetadata" in result.extension_types
    assert result.transfer_fee_bps is None
    assert result.transfer_hook_program is None
    assert result.hard_risk_flags == ()


def test_metadata_plus_transfer_fee_decodes_fee_independently():
    fee = bytearray(116)
    struct.pack_into("<Q", fee, 106, 1234)
    struct.pack_into("<H", fee, 114, 250)
    result = parse_mint_data(
        _mint_with_extensions((19, _METADATA_PAYLOAD), (1, bytes(fee))),
        owner_program=TOKEN_2022_PROGRAM_ID,
    )
    assert result.token_metadata is True
    assert result.transfer_fee_bps == 250
    assert result.transfer_tax_pct == 2.5
    assert result.transfer_fee_maximum_raw == 1234
    assert result.hard_risk_flags == ()


def test_metadata_plus_transfer_hook_decodes_hook_independently():
    hook = bytes(68)
    result = parse_mint_data(
        _mint_with_extensions((19, _METADATA_PAYLOAD), (14, hook)),
        owner_program=TOKEN_2022_PROGRAM_ID,
    )
    assert result.token_metadata is True
    assert result.transfer_hook_program is not None
    assert result.transfer_fee_bps is None
    assert "transfer_hook" in result.hard_risk_flags


def test_metadata_and_fee_and_hook_together():
    fee = bytearray(116)
    struct.pack_into("<Q", fee, 106, 4321)
    struct.pack_into("<H", fee, 114, 500)
    hook = bytes(68)
    result = parse_mint_data(
        _mint_with_extensions((19, _METADATA_PAYLOAD), (1, bytes(fee)), (14, hook)),
        owner_program=TOKEN_2022_PROGRAM_ID,
    )
    assert result.token_metadata is True
    assert result.transfer_fee_bps == 500
    assert result.transfer_fee_maximum_raw == 4321
    assert result.transfer_hook_program is not None
    assert "transfer_hook" in result.hard_risk_flags


def test_malformed_metadata_fails_closed():
    with pytest.raises(Token2022ParseError):
        parse_mint_data(_mint_with_extensions((19, b"")),
                        owner_program=TOKEN_2022_PROGRAM_ID)


def test_trailing_zero_padding_after_metadata_is_skipped():
    raw = base64.b64decode(_mint_with_extensions((19, _METADATA_PAYLOAD))) + bytes(79)
    result = parse_mint_data(base64.b64encode(raw).decode(),
                             owner_program=TOKEN_2022_PROGRAM_ID)
    assert result.token_metadata is True
    assert "TokenMetadata" in result.extension_types
