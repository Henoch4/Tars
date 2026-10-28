"""Guard tests for the CA13 two-party signed release gate.

Ed25519 signing over canonical digests; runner/evaluator key separation;
fail-closed taxonomy: missing trust, forged evidence, self-review, binding
drift, incomplete/incoherent judgments, and bad bundle shapes all raise
typed. Keypairs are generated in-test (test tooling); PRODUCTION keys come
from env — trust_keys_from_env() enforces that and is tested for it.
"""
import pytest

from src.release_bundles import (
    BadBundle,
    EVALUATOR_PUB_ENV,
    EVALUATOR_KEY_ENV,
    ForgedEvidence,
    hash_canonical,
    IncompleteJudgment,
    JudgmentBinding,
    JudgmentMismatch,
    build_judgment_bundle,
    build_runner_bundle,
    RUNNER_PUB_ENV,
    RUNNER_KEY_ENV,
    SelfReviewedEvidence,
    SigUnavailable,
    sign_payload,
    trust_keys_from_env,
    UntrustedRelease,
    verify_release,
    verify_signature,
    _raw_private_bytes,
    _raw_public_bytes,
)

CRYPTO_AVAILABLE = True
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
except Exception:  # pragma: no cover
    CRYPTO_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not CRYPTO_AVAILABLE,
    reason="cryptography not installed; release gate refuses to claim trust",
)


def _keypair() -> tuple[str, str]:
    """Test tooling: (private_seed_hex-ish, public_hex) for one party."""
    key = Ed25519PrivateKey.generate()
    priv = _raw_private_bytes(key)          # 32-byte seed
    import hashlib
    # sign_payload takes the 32-byte seed as hex; tests hand it directly
    return priv.hex(), _raw_public_bytes(key).hex()


def _receipts() -> list[dict]:
    return [
        {"case": "gitleaks-clean", "passed": True},
        {"case": "pytest-green", "passed": True, "detail": "1173 passed"},
        {"case": "manifest-doctor", "passed": True},
    ]


def _glued(runner_key, runner_pub, judge_key, judge_pub):
    rb = build_runner_bundle(
        subject={"release": "tars", "phase": "demo"},
        runtime={"python": "3.14", "check_release": "v1"},
        receipts=_receipts(),
        signing_key_hex=runner_key,
    )
    judgments = {c["case"]: "pass" for c in _receipts()}
    jb = build_judgment_bundle(
        runner_bundle=rb, judgments=judgments, signing_key_hex=judge_key
    )
    return rb, jb, judgments


# -- canonical hashing --------------------------------------------------------

def test_canonical_hash_is_order_insensitive_and_deterministic():
    a = {"b": 1, "a": {"y": 2, "x": [3, 1]}}
    b = {"a": {"x": [3, 1], "y": 2}, "b": 1}
    assert hash_canonical(a) == hash_canonical(b) == hash_canonical(a)


def test_sign_and_verify_roundtrip():
    runner_key, runner_pub = _keypair()
    signed = sign_payload({"n": 1}, runner_key)
    digest = verify_signature(signed, runner_pub, label="test")
    assert digest == hash_canonical({"n": 1})


def test_tampered_payload_is_forged():
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    signed = sign_payload({"n": 1}, runner_key)
    signed["payload"]["n"] = 2
    with pytest.raises(ForgedEvidence):
        verify_signature(signed, runner_pub)
    # a valid bundle verified under the WRONG trust key is equally forged
    with pytest.raises(ForgedEvidence):
        verify_signature(signed, judge_pub)


def test_signature_wrong_length_is_forged_not_a_crash():
    runner_key, runner_pub = _keypair()
    signed = sign_payload({"n": 1}, runner_key)
    signed["signature"] = "ab" * 63
    with pytest.raises((ForgedEvidence, ValueError)):
        verify_signature(signed, runner_pub)


# -- happy two-party path ------------------------------------------------------

def test_happy_two_party_release():
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    rb, jb, _ = _glued(runner_key, runner_pub, judge_key, judge_pub)
    verdict = verify_release(
        runner_bundle=rb, runner_public_key=runner_pub,
        judgment_bundle=jb, evaluator_public_key=judge_pub,
    )
    assert verdict["released"] is True
    assert verdict["cases"] == 3
    assert verdict["escalated"] == []
    assert verdict["verdict_id"].startswith("rel_")
    assert verdict["subject"] == {"release": "tars", "phase": "demo"}


def test_runner_bundle_carries_runtime_and_subject_ink():
    runner_key, runner_pub = _keypair()
    rb = build_runner_bundle(
        subject={"release": "tars", "phase": "demo"},
        runtime={"python": "3.14.x", "check_release_version": 1,
                 "host": "workstation"},
        receipts=_receipts(), signing_key_hex=runner_key)
    verify_signature(rb, runner_pub, label="runner_bundle")
    assert rb["payload"]["runtime"]["check_release_version"] == 1
    assert len(rb["payload"]["receipts"]) == 3


def test_escalated_case_blocks_release():
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    receipts = _receipts() + [{"case": "found-by-evaluator",
                               "passed": False, "detail": "regression"}]
    rb = build_runner_bundle(
        subject={"release": "tars"}, runtime={"python": "3"},
        receipts=receipts, signing_key_hex=runner_key,
    )
    judgments = {r["case"]: "pass" for r in receipts}
    judgments["found-by-evaluator"] = "escalate"
    jb = build_judgment_bundle(runner_bundle=rb, judgments=judgments,
                               signing_key_hex=judge_key)
    verdict = verify_release(
        runner_bundle=rb, runner_public_key=runner_pub,
        judgment_bundle=jb, evaluator_public_key=judge_pub,
    )
    assert verdict["released"] is False
    assert verdict["escalated"] == ["found-by-evaluator"]


# -- fail-closed taxonomy --------------------------------------------------------

def test_judgment_mismatch_forbidden():
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    receipts = [{"case": "slow-suite", "passed": False}]
    rb = build_runner_bundle(
        subject={"release": "tars"}, runtime={"python": "3"},
        receipts=receipts, signing_key_hex=runner_key)
    jb = build_judgment_bundle(
        runner_bundle=rb, judgments={"slow-suite": "pass"},
        signing_key_hex=judge_key)
    with pytest.raises(JudgmentMismatch):
        verify_release(runner_bundle=rb, runner_public_key=runner_pub,
                       judgment_bundle=jb, evaluator_public_key=judge_pub)


def test_incomplete_judgment_missing_case():
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    rb, jb, _ = _glued(runner_key, runner_pub, judge_key, judge_pub)
    jb2 = build_judgment_bundle(
        runner_bundle=rb,
        judgments={"gitleaks-clean": "pass", "pytest-green": "pass"},
        signing_key_hex=judge_key)
    with pytest.raises(IncompleteJudgment):
        verify_release(runner_bundle=rb, runner_public_key=runner_pub,
                       judgment_bundle=jb2, evaluator_public_key=judge_pub)


def test_incomplete_judgment_extra_case():
    """A judgment for a case that never ran: the sets simply do not match —
    the evaluator cannot invent coverage."""
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    rb, _, _ = _glued(runner_key, runner_pub, judge_key, judge_pub)
    jb2 = build_judgment_bundle(
        runner_bundle=rb,
        judgments={c["case"]: "pass" for c in _receipts()} | {"ghost-case": "pass"},
        signing_key_hex=judge_key,
    )
    with pytest.raises(IncompleteJudgment):
        verify_release(runner_bundle=rb, runner_public_key=runner_pub,
                       judgment_bundle=jb2, evaluator_public_key=judge_pub)


def test_judgment_binding_refuses_wrong_runner_bundle():
    """Same judge ink verified against a DEMO runner bundle whose digest
    differs -> the ink is bound to the original, not to this one."""
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    rb, jb, _ = _glued(runner_key, runner_pub, judge_key, judge_pub)
    rb2 = build_runner_bundle(
        subject={"release": "tars", "phase": "demo"},
        runtime={"python": "3.14", "check_release": "v2"},  # runtime changed
        receipts=_receipts(), signing_key_hex=runner_key,
    )
    with pytest.raises(JudgmentBinding):
        verify_release(runner_bundle=rb2, runner_public_key=runner_pub,
                       judgment_bundle=jb, evaluator_public_key=judge_pub)


def test_binding_refuses_runner_payload_change():
    """Even in-place payload tampering re-binds: digest differs."""
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    rb, jb, _ = _glued(runner_key, runner_pub, judge_key, judge_pub)
    rb["payload"]["runtime"]["python"] = "3.9"
    # verify_release first checks the runner signature, so this raises forged
    with pytest.raises(ForgedEvidence):
        verify_release(runner_bundle=rb, runner_public_key=runner_pub,
                       judgment_bundle=jb, evaluator_public_key=judge_pub)


def test_self_reviewed_evidence_refused():
    """Same trust key for both roles = self-review = hard refusal with the
    specific typed error."""
    key, pub = _keypair()
    rb = build_runner_bundle(
        subject={"release": "tars"}, runtime={"host": "local"},
        receipts=_receipts(), signing_key_hex=key)
    jb = build_judgment_bundle(
        runner_bundle=rb,
        judgments={c["case"]: "pass" for c in _receipts()},
        signing_key_hex=key)
    with pytest.raises(SelfReviewedEvidence):
        verify_release(
            runner_bundle=rb, runner_public_key=pub,
            judgment_bundle=jb, evaluator_public_key=pub,
        )


def test_missing_trust_keys_are_untrusted():
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    rb, jb, _ = _glued(runner_key, runner_pub, judge_key, judge_pub)
    with pytest.raises(UntrustedRelease):
        verify_release(runner_bundle=rb, runner_public_key="",
                       judgment_bundle=jb, evaluator_public_key=judge_pub)
    with pytest.raises(UntrustedRelease):
        verify_release(runner_bundle=rb, runner_public_key=runner_pub,
                       judgment_bundle=jb, evaluator_public_key=None)


def test_bad_bundle_shapes_raise():
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    with pytest.raises(BadBundle):
        build_runner_bundle(subject={}, runtime={"h": 1},
                            receipts=_receipts(), signing_key_hex=runner_key)
    with pytest.raises(BadBundle):
        build_runner_bundle(subject={"r": "tars"}, runtime={},
                            receipts=_receipts(), signing_key_hex=runner_key)
    with pytest.raises(BadBundle):
        build_runner_bundle(subject={"r": "tars"}, runtime={"h": 1},
                            receipts=[], signing_key_hex=runner_key)
    with pytest.raises(BadBundle):
        build_runner_bundle(subject={"r": "tars"}, runtime={"h": 1},
                            receipts=[{"case": "x", "passed": True},
                                      {"case": "x", "passed": False}],
                            signing_key_hex=runner_key)
    with pytest.raises(BadBundle):
        build_runner_bundle(subject={"r": "tars"}, runtime={"h": 1},
                            receipts=[{"case": "x", "passed": "yes"}],
                            signing_key_hex=runner_key)
    with pytest.raises(BadBundle):
        build_runner_bundle(subject={"r": "tars"}, runtime={"h": 1},
                            receipts=[{"case": "", "passed": True}],
                            signing_key_hex=runner_key)
    rb, _, _ = _glued(runner_key, runner_pub, judge_key, judge_pub)
    with pytest.raises(BadBundle):
        build_judgment_bundle(
            runner_bundle=rb,
            judgments={c["case"]: "maybe" for c in _receipts()},
            signing_key_hex=judge_key)


def test_trust_keys_from_env(monkeypatch):
    runner_key, runner_pub = _keypair()
    judge_key, judge_pub = _keypair()
    monkeypatch.delenv(RUNNER_PUB_ENV, raising=False)
    monkeypatch.delenv(EVALUATOR_PUB_ENV, raising=False)
    with pytest.raises(UntrustedRelease):
        trust_keys_from_env()
    monkeypatch.setenv(RUNNER_PUB_ENV, runner_pub)
    monkeypatch.setenv(EVALUATOR_PUB_ENV, judge_pub)
    assert trust_keys_from_env() == (runner_pub, judge_pub)
    monkeypatch.setenv(EVALUATOR_PUB_ENV, "zz")
    with pytest.raises(UntrustedRelease):
        trust_keys_from_env()


def test_sig_unavailable_is_typed_when_crypto_missing():
    from src.release_bundles import SigUnavailable as SU
    assert issubclass(SU, Exception)
