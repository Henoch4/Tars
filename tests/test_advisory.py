"""Guard tests for the Book-3 advisory layer (Sendo "recommend, never order").

The critical invariants are STRUCTURAL, not behavioral:
  1. src/advisory.py never references the order path (no src.execution,
     src.agent, src.signals imports anywhere in the module graph).
  2. Advisory is not order-shaped: no direction/size/price/confidence/
     leverage/order_type field, no to_signal/to_order/from_* method,
     no is_tradeable property.
  3. The explanation table is honest: every code it explains exists in the
     live risk gate source, and every rejection code the gate emits is
     explained (unknown = explicit unexplained verdict, never invented).

Fully offline. AST-based structural checks are order-independent (no
sys.modules pollution), so they hold under any test ordering.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.advisory import (
    ADVISORY_KIND_PROFILE,
    ADVISORY_KIND_REJECTION,
    CODE_EXPLANATIONS,
    Advisory,
    explain_profile,
    explain_rejection,
)

ROOT = Path(__file__).resolve().parent.parent
ADVISORY_SRC = ROOT / "src" / "advisory.py"
RISK_GATE_SRC = ROOT / "src" / "execution" / "risk_gate.py"


# ---------------------------------------------------------------------------
# 1. Structural: never on the order path
# ---------------------------------------------------------------------------

_FORBIDDEN_REFERENCES = (
    "src.execution", "src.agent", "src.signals", "src.executor",
    "risk_gate", "OrderExecutor", "OkxCli", "okx_cli",
    "PLACE_ORDER", "place_order",
)


def test_advisory_module_never_references_the_order_path():
    """AST-only scan: no import of any order-path module anywhere in the file."""
    tree = ast.parse(ADVISORY_SRC.read_text(encoding="utf-8"), filename="advisory.py")
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for banned in _FORBIDDEN_REFERENCES:
        assert not any(
            h == banned or h.startswith(banned + ".") for h in imported
        ), f"advisory imports {banned}"


def test_advisory_import_does_not_pull_in_the_order_path_modules():
    """Importing src.advisory fresh (subprocess, clean interpreter) must not
    load the heavy order-path modules into sys.modules."""
    import subprocess
    code = (
        "import sys, json;"
        "import src.advisory;"
        "mods = sorted(m for m in sys.modules"
        " if m.startswith(('src.execution', 'src.agent', 'src.signals', 'src.executor')));"
        "print(json.dumps(mods))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == []


# ---------------------------------------------------------------------------
# 2. Structural: Advisory is not order-shaped
# ---------------------------------------------------------------------------

_ORDER_LIKE_FIELDS = (
    "direction", "confidence_bps", "size", "size_usd", "entry_price",
    "leverage", "order_type", "side", "inst_id",
)
_ORDER_LIKE_METHODS = (
    "to_signal", "to_order", "from_signal", "from_order",
    "is_tradeable", "needs_sizing", "approve",
)


def test_advisory_has_no_order_like_fields():
    fields = set(Advisory.__dataclass_fields__)
    banned = set(_ORDER_LIKE_FIELDS) & fields
    assert not banned, f"Advisory grew order-shaped fields: {sorted(banned)}"


def test_advisory_has_no_tradeable_affordances():
    for name in _ORDER_LIKE_METHODS:
        assert not hasattr(Advisory, name), f"Advisory must not expose {name!r}"


def test_field_set_is_the_published_shape():
    expected = {
        "advisory_id", "created_ts", "kind", "subject", "headline",
        "evidence", "explanation",
    }
    assert set(Advisory.__dataclass_fields__) == expected  # new field = deliberate


# ---------------------------------------------------------------------------
# 3. Honesty of the explanation layer
# ---------------------------------------------------------------------------

def test_every_table_key_is_a_real_risk_gate_code():
    gate_text = RISK_GATE_SRC.read_text(encoding="utf-8")
    for code in CODE_EXPLANATIONS:
        assert f'"{code}"' in gate_text or f"code=\"{code}\"" in gate_text, (
            f"CODE_EXPLANATIONS explains {code!r} which the live gate never emits"
        )


def test_every_gate_rejection_code_is_explained():
    gate_text = RISK_GATE_SRC.read_text(encoding="utf-8")
    # Rejection codes appear in exactly two shapes: self._reject(<LITERAL> ...)
    # and code="<LITERAL>" check results. Action results (PROCEED/WAIT/BLOCK),
    # events (KILL_SWITCH_ACTIVATED/...), and runtime statuses are NOT
    # rejections and are deliberately not scanned.
    emitted = set(re.findall(r'self\._reject\(\s*"([A-Z][A-Z_]{3,})"', gate_text))
    emitted |= set(re.findall(r'code="([A-Z][A-Z_]{3,})"', gate_text))
    emitted.discard("APPROVED")
    missing = emitted - set(CODE_EXPLANATIONS)
    assert not missing, (
        f"gate emits rejection codes with no explanation: {sorted(missing)}"
    )


def test_table_never_carries_relax_advice():
    for code, text in CODE_EXPLANATIONS.items():
        lowered = text.lower()
        assert "relax" not in lowered and "override the gate" not in lowered, code


# ---------------------------------------------------------------------------
# 4. Behavior: deterministic, fail-closed, honest-unknown
# ---------------------------------------------------------------------------

def test_explain_rejection_single_code():
    adv = explain_rejection(
        asset="BTC-USDT-SWAP", ts=1_700_000_000.0,
        codes=["DAILY_LOSS_LIMIT_EXCEEDED"], note="end-of-day sweep",
    )
    assert adv.kind == ADVISORY_KIND_REJECTION
    assert adv.subject == "BTC-USDT-SWAP order refusal"
    assert "daily loss" in adv.explanation.lower()
    assert ("risk_gate", "rejection code DAILY_LOSS_LIMIT_EXCEEDED") in adv.evidence
    assert ("operator_note", "end-of-day sweep") in adv.evidence
    assert adv.advisory_id.startswith("adv_")
    # deterministic: same inputs -> same id
    again = explain_rejection(
        asset="BTC-USDT-SWAP", ts=1_700_000_000.0,
        codes=["DAILY_LOSS_LIMIT_EXCEEDED"], note="end-of-day sweep",
    )
    assert again.advisory_id == adv.advisory_id


def test_explain_rejection_duplicate_codes_counted_once():
    adv = explain_rejection(
        asset="SOL-USDT-SWAP", ts=1.0,
        codes=["KILL_SWITCH_ACTIVE", "KILL_SWITCH_ACTIVE"],
    )
    ways = [e for e in adv.evidence if e[0] == "risk_gate"]
    assert len(ways) == 1
    assert adv.headline.startswith("1 risk-gate check")


def test_explain_rejection_multi_code_names_the_count():
    adv = explain_rejection(
        asset="ETH-USDT-SWAP", ts=1.0,
        codes=["LEVERAGE_EXCEEDED", "POSITION_TOO_LARGE"],
    )
    assert adv.headline.startswith("2 risk-gate checks")


def test_unknown_code_is_unexplained_never_invented():
    adv = explain_rejection(asset="SOL-USDT-SWAP", ts=1.0, codes=["TOTALLY_FAKE_CODE"])
    assert "TOTALLY_FAKE_CODE" in adv.explanation
    assert "not in the advisory explanation table" in adv.explanation
    assert "NOT being guessed" in adv.explanation
    # no fabricated rationale sentence for the unknown code
    assert "the gate is wrong" not in adv.explanation.lower()


def test_to_dict_roundtrip_shape_and_no_secret_leakage():
    adv = explain_rejection(
        asset="BNB-USDT-SWAP", ts=42.0,
        codes=["FAT_FINGER_REJECTED"], note="ref px vs market",
    )
    d = adv.to_dict()
    assert set(d) == {
        "advisory_id", "created_ts", "kind", "subject", "headline",
        "evidence", "explanation",
    }
    # evidence pairs are [provenance, fact] lists (JSON-safe)
    assert all(isinstance(p, list) and len(p) == 2 for p in d["evidence"])
    blob = repr(d)
    for banned in ("PRIVATE_KEY", "API_KEY", "SECRET", "PASSPHRASE"):
        assert banned not in blob


@pytest.mark.parametrize("kwargs", [
    dict(asset="", ts=1.0, codes=["KILL_SWITCH_ACTIVE"]),
    dict(asset="BTC-USDT-SWAP", ts=1.0, codes=[]),
    dict(asset="BTC-USDT-SWAP", ts=1.0, codes=[""]),
    dict(asset="BTC-USDT-SWAP", ts=1.0, codes=None),
])
def test_explain_rejection_fail_closed_on_bad_inputs(kwargs):
    with pytest.raises((ValueError, TypeError)):
        explain_rejection(**kwargs)


@pytest.mark.parametrize("ts", [float("nan"), float("inf"), "1700000000", True])
def test_explain_rejection_rejects_bad_ts(ts):
    with pytest.raises((ValueError, TypeError)):
        explain_rejection(asset="BTC-USDT-SWAP", ts=ts, codes=["KILL_SWITCH_ACTIVE"])


def test_explain_rejection_rejects_non_sequence_codes():
    with pytest.raises(TypeError):
        explain_rejection(asset="BTC-USDT-SWAP", ts=1.0, codes=42)


def test_explain_profile_flow():
    adv = explain_profile(
        profile="defensive", ts=7.0, applies_to="BTC-USDT-SWAP",
        note="streak trip",
    )
    assert adv.kind == ADVISORY_KIND_PROFILE
    assert adv.subject == "BTC-USDT-SWAP curator posture"
    assert "defensive" in adv.headline
    assert ("curator", "active profile 'defensive'") in adv.evidence
    assert ("operator_note", "streak trip") in adv.evidence
    with pytest.raises((ValueError, TypeError)):
        explain_profile(profile="", ts=1.0, applies_to="x")
    with pytest.raises((ValueError, TypeError)):
        explain_profile(profile="defensive", ts=float("nan"), applies_to="x")


def test_advisory_is_frozen():
    adv = explain_rejection(asset="X", ts=1.0, codes=["KILL_SWITCH_ACTIVE"])
    with pytest.raises(Exception):
        adv.headline = "tampered"
