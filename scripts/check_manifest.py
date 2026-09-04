#!/usr/bin/env python3
"""Doctor gate for manifest.json (I7) — run in CI and before releases.

Fails when:
- manifest.json is missing or unparsable,
- required top-level keys / endpoint shape are wrong,
- any endpoint lacks a real description ("unknown endpoint", empty, or
  the "{func} endpoint" generator fallback — write a docstring instead),
- descriptions contain mojibake (double-encoded UTF-8 from old generators),
- manifest endpoints drift from the live app routes in EITHER direction
  (stale entries or undocumented routes).

Exit 0 = healthy, 1 = problems found (messages to stdout).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
MANIFEST_PATH = REPO / "manifest.json"

REQUIRED_TOP_KEYS = {"name", "version", "endpoints", "capabilities"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# U+00E2 U+20AC etc. = UTF-8 bytes misread as latin-1 (old generator output).
MOJIBAKE_RE = re.compile("[\u00e2\u20ac\u2122\u201c\u201d\u00a0]")


def fail(msg: str, problems: list[str]) -> None:
    problems.append(msg)


def check_manifest() -> list[str]:
    problems: list[str] = []
    if not MANIFEST_PATH.exists():
        return [f"manifest.json missing at {MANIFEST_PATH}"]

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return [f"manifest.json unparsable: {e}"]

    for key in REQUIRED_TOP_KEYS:
        if key not in manifest:
            fail(f"missing top-level key: {key}", problems)

    version = str(manifest.get("version", ""))
    if version and not VERSION_RE.match(version):
        fail(f"version {version!r} is not semver x.y.z", problems)

    endpoints = manifest.get("endpoints", [])
    if not isinstance(endpoints, list):
        return problems + ["endpoints is not a list"]

    seen = set()
    for i, ep in enumerate(endpoints):
        if not isinstance(ep, dict):
            fail(f"endpoints[{i}] is not an object", problems)
            continue
        for field in ("path", "method", "description"):
            if not ep.get(field):
                fail(f"endpoints[{i}] missing/empty {field!r}", problems)
        desc = str(ep.get("description", ""))
        if desc == "unknown endpoint" or re.fullmatch(r"\w+ endpoint", desc):
            fail(f"endpoints[{i}] ({ep.get('path')}) has a placeholder "
                 f"description — document the endpoint instead", problems)
        if MOJIBAKE_RE.search(desc) or MOJIBAKE_RE.search(str(ep.get("path", ""))):
            fail(f"endpoints[{i}] ({ep.get('path')}) contains mojibake "
                 f"(double-encoded UTF-8)", problems)
        key = (ep.get("path"), str(ep.get("method", "")).upper())
        if key in seen:
            fail(f"duplicate endpoint {key}", problems)
        seen.add(key)

    # Bidirectional drift check against the live app.
    try:
        from src.main import app
    except Exception as e:  # noqa: BLE001 — import failure is itself a finding
        return problems + [f"cannot import app for drift check: {e}"]

    live = set()
    for route in app.routes:
        methods = sorted(getattr(route, "methods", None) or ())
        if not methods or getattr(route, "include_in_schema", True) is False:
            continue
        for m in methods:
            if m != "HEAD":
                live.add((route.path, m))

    manifest_keys = {(e.get("path"), str(e.get("method", "")).upper())
                     for e in endpoints if isinstance(e, dict)}
    for stale in sorted(manifest_keys - live):
        fail(f"stale manifest entry (no such route): {stale}", problems)
    for undoc in sorted(live - manifest_keys):
        fail(f"live route missing from manifest: {undoc} "
             f"(run scripts/generate_openapi.py)", problems)

    return problems


def main() -> int:
    problems = check_manifest()
    if problems:
        print(f"manifest doctor: {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("manifest doctor: healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
