"""Independent-verification release gate (CA13, claude-ads stealable).

"No independent verification means no release" — encoded as a two-party,
signed evaluation protocol:

  - The **runner** executes a release's checks and emits a signed
    RunnerBundle: subject + runtime + per-case receipts (what actually
    ran, what actually failed).
  - The **evaluator** — a DIFFERENT party with a DIFFERENT key — reads
    that signed bundle and emits a signed JudgmentBundle: one judgment per
    case (pass / escalate), bound to the digest of the exact runner bundle
    it judged.
  - `verify_release` closes the loop. It succeeds ONLY with two valid
    signatures under two distinct external trust keys (env-provided), a
    judgment covering every receipt, coherent pass/escalate semantics
    (an escalated case blocks release; a "pass" judgment over a failed
    receipt is impossible), and no self-review (runner key == evaluator
    key is refused, not allowed).

Fail-closed taxonomy (pinned by tests/test_release_bundles.py):
  - `SigUnavailable`   cryptography absent — signing cannot claim trust
  - `UntrustedRelease` trust key missing/invalid/present-but-unparseable
  - `ForgedEvidence`   signature does not verify under the trust key
  - `SelfReviewedEvidence` runner and evaluator keys are the same
  - `IncompleteJudgment`evaluations miss or exceed the receipt set
  - `JudgmentBinding`   judgments bound to a different bundle digest
  - `JudgmentMismatch`  "pass" judgment over a failed receipt
  - `BadBundle`         malformed payload shapes

Keys come from the ENVIRONMENT (external trust): private signing keys
(`TARS_RELEASE_RUNNER_SIGNING_KEY`, `TARS_RELEASE_EVALUATOR_SIGNING_KEY`,
32-byte hex seeds) are held by their parties; verification keys are the
corresponding 32-byte hex PUBLIC keys. Repository-local, self-generated,
or in-repo keys that claim to be external trust are exactly the
self-review this module exists to refuse — the runner declaring its own
release "verified" is the one shape verify_release structurally cannot
produce.

UNWIRED by design (TODO 3.6): the operator invokes this over
`scripts/check_release.py` output. Nothing automatic consumes it yet; the
mechanical half (gitleaks, pytest, manifest doctor) stays in
`check_release.py`. `cryptography` is a dev dependency
(requirements-dev.txt) — CI and the operator's box have it; Vercel does
not need this module.
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping, Sequence

RUNNER_KEY_ENV = "TARS_RELEASE_RUNNER_SIGNING_KEY"
EVALUATOR_KEY_ENV = "TARS_RELEASE_EVALUATOR_SIGNING_KEY"
RUNNER_PUB_ENV = "TARS_RELEASE_RUNNER_PUBLIC_KEY"
EVALUATOR_PUB_ENV = "TARS_RELEASE_EVALUATOR_PUBLIC_KEY"

JUDGMENT_PASS = "pass"
JUDGMENT_ESCALATE = "escalate"
JUDGMENTS = (JUDGMENT_PASS, JUDGMENT_ESCALATE)

BUNDLE_SCHEMA_VERSION = 1

try:  # cryptography ships with dev tooling; the module refuses to sign
    # or verify if the trust primitive is missing (fail-closed import).
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    _CRYPTO_ERROR: Exception | None = None
except Exception as _e:  # pragma: no cover - exercised via SigUnavailable
    _CRYPTO_ERROR = _e


def _raw_private_bytes(key: "Ed25519PrivateKey") -> bytes:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def _raw_public_bytes(key) -> bytes:
    """Accepts either a private or a public key object; returns the
    32-byte raw public encoding."""
    pub = key.public_key() if hasattr(key, "private_bytes") else key
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


class ReleaseGateError(Exception):
    """Base — every release-gate failure is typed."""


class SigUnavailable(ReleaseGateError):
    """The Ed25519 primitive is missing; nothing may claim to be verified."""


class UntrustedRelease(ReleaseGateError):
    """A required trust key is absent or malformed. No trust, no release."""


class ForgedEvidence(ReleaseGateError):
    """A signature does not verify under the provided trust key."""


class SelfReviewedEvidence(ReleaseGateError):
    """Runner and evaluator keys coincide — the two-party split is broken."""


class IncompleteJudgment(ReleaseGateError):
    """Judgments do not cover the receipt set (missing or extra cases)."""


class JudgmentBinding(ReleaseGateError):
    """Judgments bound to a different runner bundle digest."""


class JudgmentMismatch(ReleaseGateError):
    """A 'pass' judgment over a failed receipt — incoherent evidence."""


class BadBundle(ReleaseGateError):
    """Payload shape is not a bundle this module defines."""


def _require_crypto():
    if _CRYPTO_ERROR is not None:
        raise SigUnavailable(
            "cryptography is unavailable; the release gate cannot claim "
            "trust without it (pip install cryptography)"
        ) from _CRYPTO_ERROR


def hash_canonical(payload: Mapping[str, Any]) -> str:
    """Deterministic sha256 digest of a JSON-serializable payload. Any two
    byte-equal payloads hash equal; key order never matters."""
    try:
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as e:
        raise BadBundle(f"payload is not JSON-serializable: {e}") from e
    import hashlib
    return hashlib.sha256(blob).hexdigest()


def _load_private_key(key_hex: str) -> "Ed25519PrivateKey":
    _require_crypto()
    if not isinstance(key_hex, str) or len(key_hex) != 64:
        raise BadBundle("signing key must be a 64-hex-char (32-byte) seed")
    try:
        seed = bytes.fromhex(key_hex)
    except ValueError as e:
        raise BadBundle(f"signing key is not hex: {e}") from e
    return Ed25519PrivateKey.from_private_bytes(seed)


def _load_public_key(pub_hex: str) -> "Ed25519PublicKey":
    _require_crypto()
    if not isinstance(pub_hex, str) or len(pub_hex) != 64:
        raise UntrustedRelease(
            "public trust key must be 64 hex chars (32 bytes raw)"
        )
    try:
        raw = bytes.fromhex(pub_hex)
    except ValueError as e:
        raise UntrustedRelease(f"public trust key is not hex: {e}") from e
    return Ed25519PublicKey.from_public_bytes(raw)


def _as_bundle(obj: object, label: str) -> tuple[dict, str]:
    if not isinstance(obj, Mapping):
        raise BadBundle(f"{label} is not a bundle mapping")
    payload = obj.get("payload")
    sig = obj.get("signature")
    if not isinstance(payload, Mapping) or not isinstance(sig, str):
        raise BadBundle(f"{label} lacks payload/signature")
    version = payload.get("schema_version")
    if version is not None and version != BUNDLE_SCHEMA_VERSION:
        raise BadBundle(
            f"{label} schema_version {version!r} != {BUNDLE_SCHEMA_VERSION}"
        )
    return dict(payload), sig


# -- signing / verification primitives --------------------------------------

def sign_payload(payload: Mapping[str, Any], signing_key_hex: str) -> dict:
    """Sign `payload` canonically. Returns {payload, signature} — the
    canonical digest is what gets inked, so downstream re-serialization
    can never invalidate the ink."""
    if not isinstance(payload, Mapping):
        raise BadBundle("payload must be a mapping")
    digest = hash_canonical(payload)
    key = _load_private_key(signing_key_hex)
    sig = key.sign(digest.encode("utf-8"))
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return {
        "payload": dict(payload),
        "signature": sig.hex(),
        "signing_pubkey": pub,
    }


def verify_signature(
    bundle: Mapping[str, Any],
    public_key_hex: str,
    *,
    label: str = "bundle",
) -> str:
    """Verify a signed payload under an EXTERNAL trust key. Returns the
    canonical digest on success; raises ForgedEvidence/UntrustedRelease."""
    payload, sig_hex = _as_bundle(bundle, label)
    digest = hash_canonical(payload)
    pub = _load_public_key(public_key_hex)
    try:
        if len(sig_hex) != 128:
            raise ForgedEvidence(f"{label} signature is not 64 bytes")
        pub.verify(bytes.fromhex(sig_hex), digest.encode("utf-8"))
    except (InvalidSignature, ValueError) as e:
        raise ForgedEvidence(f"{label} failed signature verification") from e
    return digest


# -- the two parties ---------------------------------------------------------

def build_runner_bundle(
    *,
    subject: Mapping[str, Any],
    runtime: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    signing_key_hex: str,
) -> dict:
    """Runner: sign what was actually run. Each receipt: {case, passed,
    detail?}. runtime should carry python version, check_release version,
    host identity — the "runtime" half of CA13's ink."""
    if not isinstance(subject, Mapping) or not subject:
        raise BadBundle("subject must be a non-empty mapping")
    if not isinstance(runtime, Mapping) or not runtime:
        raise BadBundle("runtime must be a non-empty mapping")
    if not isinstance(receipts, (Sequence,)) or isinstance(receipts, (str, bytes)):
        raise BadBundle("receipts must be a sequence")
    seen: set[str] = set()
    norm_receipts: list[dict] = []
    for i, r in enumerate(receipts):
        if not isinstance(r, Mapping):
            raise BadBundle(f"receipt {i} is not a mapping")
        case = r.get("case")
        if not isinstance(case, str) or not case.strip():
            raise BadBundle(f"receipt {i} lacks a non-empty 'case'")
        if case in seen:
            raise BadBundle(f"duplicate receipt case {case!r}")
        seen.add(case)
        passed = r.get("passed")
        if not isinstance(passed, bool):
            raise BadBundle(f"receipt {i}.passed must be bool")
        entry = {"case": case, "passed": passed}
        if "detail" in r and r["detail"] is not None:
            if not isinstance(r["detail"], str):
                raise BadBundle(f"receipt {i}.detail must be str")
            entry["detail"] = r["detail"]
        norm_receipts.append(entry)
    if not norm_receipts:
        raise BadBundle("refusing an empty receipt set (vacuous pass)")
    payload = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "party": "runner",
        "subject": dict(subject),
        "runtime": dict(runtime),
        "receipts": norm_receipts,
    }
    return sign_payload(payload, signing_key_hex)


def build_judgment_bundle(
    *,
    runner_bundle: Mapping[str, Any],
    judgments: Mapping[str, str],
    signing_key_hex: str,
) -> dict:
    """Evaluator: judge every case of the runner bundle, bound to that
    bundle's canonical digest (the ink covers what was judged)."""
    payload, sig_hex = _as_bundle(runner_bundle, "runner_bundle")
    if payload.get("party") != "runner":
        raise BadBundle(
            f"judgments bind to a runner bundle, not {payload.get('party')!r}"
        )
    digest = hash_canonical(payload)
    if not isinstance(judgments, Mapping) or not judgments:
        raise BadBundle("judgments must be a non-empty mapping")
    for case, judgment in judgments.items():
        if not isinstance(case, str) or not case.strip():
            raise BadBundle(f"judgment case {case!r} invalid")
        if judgment not in JUDGMENTS:
            raise BadBundle(
                f"judgment for {case!r} must be one of {JUDGMENTS}, got "
                f"{judgment!r}"
            )
    body = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "party": "evaluator",
        "judged_digest": digest,
        "judgments": {c: j for c, j in judgments.items()},
    }
    return sign_payload(body, signing_key_hex)


def verify_release(
    *,
    runner_bundle: Mapping[str, Any],
    runner_public_key: str,
    judgment_bundle: Mapping[str, Any],
    evaluator_public_key: str,
) -> dict:
    """Close the loop. Returns a verdict ONLY for coherent, independently
    signed two-party evidence; every other shape raises, typed."""
    if not runner_public_key or not evaluator_public_key:
        raise UntrustedRelease("both trust keys are required; none may be "
                               "defaulted in")
    if runner_public_key == evaluator_public_key:
        raise SelfReviewedEvidence(
            "runner and evaluator trust keys are identical — the two-party "
            "split this gate exists to enforce is broken; a party cannot "
            "certify its own release"
        )
    runner_payload = dict(runner_bundle.get("payload") or {})
    runner_digest = verify_signature(
        runner_bundle, runner_public_key, label="runner_bundle")
    judged_digest = verify_signature(
        judgment_bundle, evaluator_public_key, label="judgment_bundle")

    if runner_payload.get("party") != "runner":
        raise BadBundle("runner bundle does not identify as runner")
    jud_payload, _ = _as_bundle(judgment_bundle, "judgment_bundle")
    if jud_payload.get("party") != "evaluator":
        raise BadBundle("judgment bundle does not identify as evaluator")
    if jud_payload.get("judged_digest") != runner_digest:
        raise JudgmentBinding("judgments bound to a different runner bundle")

    receipts = runner_payload.get("receipts")
    judgments = jud_payload.get("judgments")
    if not isinstance(receipts, list) or not receipts:
        raise BadBundle("runner bundle receipts missing")
    if not isinstance(judgments, Mapping) or not judgments:
        raise BadBundle("judgment bundle judgments missing")
    case_ids = [r["case"] for r in receipts
                if isinstance(r, Mapping) and isinstance(r.get("case"), str)]
    if sorted(case_ids) != sorted(judgments):
        raise IncompleteJudgment(
            f"receipts {len(case_ids)} vs judgments {len(judgments)} — "
            f"the sets differ"
        )
    by_case = {r["case"]: r for r in receipts}
    for case, receipt in by_case.items():
        if judgments[case] == JUDGMENT_PASS and not receipt.get("passed"):
            raise JudgmentMismatch(
                f"'pass' judgment over a failed receipt ({case!r})"
            )
    escalated = [c for c, j in judgments.items() if j == JUDGMENT_ESCALATE]
    released = not escalated
    import hashlib
    verdict_id = "rel_" + hashlib.sha256(
        (runner_digest + ":" + judged_digest).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "verdict_id": verdict_id,
        "released": released,
        "cases": len(case_ids),
        "escalated": escalated,
        "subject": runner_payload.get("subject"),
    }


# -- env integration ----------------------------------------------------------

def trust_keys_from_env() -> tuple[str, str]:
    """The external trust surface: public keys from env, never from the
    repo. Missing/unparseable → UntrustedRelease (fail closed)."""
    ran, evl = os.environ.get(RUNNER_PUB_ENV, ""), os.environ.get(
        EVALUATOR_PUB_ENV, "")
    if not ran or not evl:
        raise UntrustedRelease(
            f"external trust keys must be env vars {RUNNER_PUB_ENV!r} and "
            f"{EVALUATOR_PUB_ENV!r}; a repo-local key is not trust"
        )
    _load_public_key(ran)   # validates parsably
    _load_public_key(evl)
    return ran, evl
