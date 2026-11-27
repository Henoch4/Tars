"""Read-only, finalized mint evidence for bounded calibration."""
import base64
import struct
import time
import math

from .gta_token2022 import TOKEN_2022_PROGRAM_ID, TOKEN_PROGRAM_ID, _b58


def _metadata(data, mint):
    """Validate the Borsh payload within this TLV slice; omit arbitrary text from logs."""
    if len(data) < 64:
        raise ValueError("Truncated metadata authority or mint")
    address = _b58(data[32:64])
    if mint is not None and address != mint:
        raise ValueError("Embedded metadata mint differs from requested account")
    cursor = 64

    def length():
        nonlocal cursor
        if len(data) - cursor < 4:
            raise ValueError(f"Missing metadata length at payload offset {cursor}")
        count = int.from_bytes(data[cursor:cursor+4], "little")
        cursor += 4
        return count

    def string():
        nonlocal cursor
        count = length()
        if count > len(data) - cursor:
            raise ValueError(f"Metadata string exceeds payload at offset {cursor}")
        data[cursor:cursor+count].decode("utf-8")
        cursor += count

    for _ in range(3):  # name, symbol, URI
        string()
    pairs = length()
    if pairs > (len(data)-cursor)//8:
        raise ValueError("Additional metadata count exceeds payload")
    for _ in range(pairs):
        string()
        string()
    if cursor != len(data):
        raise ValueError("Trailing bytes inside metadata payload")
    return dict(metadata_mint=address, additional_pairs=pairs)


def inspect_account(response, commitment, observed_at=None, *, mint=None, now=None,
                    max_age_s=900, evidence=()):
    """Evidence entries are explicit, mint-bound boolean claims about the same fact."""
    row = dict(source="getAccountInfo", commitment=commitment, slot=None, mint=mint,
               owner=None, data_length=None, extension_type=None, byte_offset=None,
               expected_layout="82-byte mint or padded mint + discriminator at 165 + TLV at 166",
               observed_at=time.time() if observed_at is None else observed_at,
               transfer_fee="unknown", sell_restrictions="unknown", extension_walk=[], freshness="unchecked")
    row["restriction_findings"] = []
    def reject(status, code, message, field, offset=None):
        return dict(row, classification=status, adapter_status=status, error_code=code,
                    reason=message, field=field, byte_offset=offset)

    try:
        result = response["result"]
        slot = result["context"]["slot"]
        row["slot"] = slot
        account = result["value"]
        if account is None:
            return reject("unknown", "account_not_found", "RPC returned no account value", "result.value")
        row["owner"] = account["owner"]
        encoded, encoding = account["data"]
        if encoding != "base64":
            return reject("unsupported", "unsupported_encoding", "Expected base64", "data.encoding")
        raw = base64.b64decode(encoded, validate=True)
        row["data_length"] = len(raw)
        if type(slot) is not int or slot < 0 or commitment != "finalized":
            return reject("unknown", "invalid_context", "Expected finalized slot", "context")
        current = time.time() if now is None else now
        def fresh(ts):
            return type(ts) in (int, float) and math.isfinite(ts) and 0 <= current-ts <= max_age_s
        row["freshness"] = "fresh" if fresh(row["observed_at"]) else "stale"
        if row["freshness"] == "stale":
            return reject("unknown", "stale_read", "Observation outside freshness window", "observed_at")
        if row["owner"] != TOKEN_2022_PROGRAM_ID:
            return reject("unsupported", "wrong_owner", "Classic SPL" if row["owner"] == TOKEN_PROGRAM_ID else "Unexpected owner", "owner")
        if account["executable"] is not False:
            return reject("malformed", "executable_account", "Mint must be non-executable", "executable")
        if len(raw) < 82:
            return reject("malformed", "short_account_data", "Base mint requires 82 bytes", "base", len(raw))
        if len(raw) < 82 or raw[45] != 1 or any(
            int.from_bytes(raw[p:p+4], "little") not in (0, 1) for p in (0, 46)
        ):
            return reject("malformed", "invalid_base_mint", "Invalid initialization or authority discriminator", "base")
        if len(raw) != 82 and (len(raw) < 166 or any(raw[82:165]) or raw[165] != 1):
            return reject("malformed", "invalid_extension_header", "Invalid padding or account discriminator", "extended_header")
        pos, seen = 166, set()
        fee, hook = None, None
        widths = {1:108, 2:8, 6:1, 9:0, 12:32, 14:64, 18:64}
        unsupported = []
        while pos < len(raw) and any(raw[pos:]):
            if len(raw)-pos < 4:
                return reject("malformed", "invalid_extension_header", "Short TLV header", "header", pos)
            kind, size = struct.unpack_from("<HH", raw, pos)
            row["extension_type"] = kind
            entry = dict(type=kind, offset=pos, length=size, status="malformed")
            row["extension_walk"].append(entry)
            pos += 4
            if pos+size > len(raw):
                return reject("malformed", "truncated_extension", "Declared payload exceeds account length", "length", pos-2)
            if kind in seen or size != widths.get(kind,size):
                return reject("malformed", "invalid_extension_header", "Duplicate type or invalid fixed length", "header", pos-4)
            seen.add(kind)
            data = raw[pos:pos+size]
            pos += size
            entry["status"] = "decoded"
            if kind == 18:
                entry.update(authority=_b58(data[:32]) if any(data[:32]) else None,
                             metadata_address=_b58(data[32:]) if any(data[32:]) else None)
            elif kind == 19:
                try:
                    entry.update(_metadata(data, mint))
                except ValueError as exc:
                    entry["status"] = "malformed"
                    return reject("malformed", "invalid_token_metadata", str(exc),
                                  "TokenMetadata", pos-size)
            elif kind == 12:
                delegate = _b58(data) if any(data) else None
                entry["permanent_delegate"] = delegate
                if delegate:
                    row["restriction_findings"].append({"type": kind, "finding": "permanent_delegate", "authority": delegate})
            elif kind == 6 and data[0] in (1,2):
                entry["default_account_state"] = data[0]
                if data[0] != 1:
                    row["restriction_findings"].append({"type": kind, "finding": "default_frozen"})
            elif kind == 9:
                entry["non_transferable"] = True
                row["restriction_findings"].append({"type": kind, "finding": "non_transferable"})
            elif kind == 14:
                hook = _b58(data[32:]) if any(data[32:]) else None
                entry["hook_program"] = hook
                row["restriction_findings"].append({"type": kind, "finding": "transfer_hook", "program": hook})
            elif kind == 1:
                fee = {"modification_authority": _b58(data[:32]) if any(data[:32]) else None}
                for label, offset in (("older", 72), ("newer", 90)):
                    epoch, cap, bps = struct.unpack_from("<QQH", data, offset)
                    if bps > 10000:
                        return reject("malformed", "invalid_fee_schedule", "Fee exceeds 10000 basis points", label, pos-size+offset+16)
                    fee[label] = dict(epoch=epoch, maximum_raw=cap, basis_points=bps)
                entry["fee_config"] = fee
            elif kind == 6:
                return reject("malformed","invalid_default_state","Unknown default account state","state",pos-size)
            else:
                entry["status"] = "account_only" if kind == 2 else "unsupported"
                if kind == 2:
                    entry["withheld_amount_raw"] = int.from_bytes(data,"little")
                unsupported.append(entry)
        if unsupported:
            first = unsupported[0]
            row["extension_type"] = first["type"]
            return reject("unsupported","unsupported_extension",
                          f"Extension {first['type']} classified as {first['status']} for mint inspection",
                          "type",first["offset"])
        facts = {"fee_config_present": bool(fee), "hook_present": 14 in seen}
        for claim in evidence:
            if (not mint or claim.get("mint") != mint or not fresh(claim.get("observed_at"))
                    or claim.get("field") not in (*facts, "sell_blocked") or type(claim.get("value")) is not bool
                    or claim.get("source") not in ("goplus", "simulation")):
                return reject("unknown", "invalid_evidence", "Incomplete, stale or incomparable claim", "evidence")
            field = claim["field"]
            if field in facts and facts[field] != claim["value"]:
                return reject("conflict", "evidence_conflict", f"{claim['source']} disagrees on {field}", field)
            facts[field] = claim["value"]
        return dict(row, classification="unknown", error_code="hook_unassessed" if hook else "sell_unassessed",
                    adapter_status="decoded", transfer_fee="configured" if fee else "absent",
                    fee_config=fee, hook_present=14 in seen, hook_program=hook,
                    reason="sell behavior requires independent evidence")
    except (KeyError, ValueError, TypeError, struct.error) as exc:
        return reject("malformed", "invalid_rpc_data", f"Invalid RPC account structure or encoding ({type(exc).__name__})", "result.value.data")
