"""Fail-closed scan for accidental live-enabling configuration."""
from __future__ import annotations

import re
from pathlib import Path

PATTERNS = (
    re.compile(r"ALLOW_LIVE\s*=\s*true", re.I),
    re.compile(r"DRY_RUN\s*=\s*false", re.I),
    re.compile(r"GTA_LIVE_ENABLED\s*=\s*true", re.I),
    re.compile(r"live_enabled\s*:\s*true", re.I),
)
ACTIVE_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?ALLOW_LIVE\s*=\s*true\s*(?:#.*)?$", re.I | re.M),
    re.compile(r"^\s*(?:export\s+)?DRY_RUN\s*=\s*false\s*(?:#.*)?$", re.I | re.M),
    re.compile(r"^\s*(?:export\s+)?GTA_LIVE_ENABLED\s*=\s*true\s*(?:#.*)?$", re.I | re.M),
    re.compile(r"^\s*live_enabled\s*:\s*true\s*(?:#.*)?$", re.I | re.M),
    re.compile(r"[\"'](?:ALLOW_LIVE|GTA_LIVE_ENABLED)[\"']\s*:\s*true", re.I),
    re.compile(r"[\"']DRY_RUN[\"']\s*:\s*false", re.I),
)


def scan(root: str | Path = ".", patterns=ACTIVE_PATTERNS) -> list[str]:
    root = Path(root)
    findings: list[str] = []
    skip_dirs = {".git", ".pytest_cache", "__pycache__", "data", ".pytest_basetemp",
                 "node_modules", ".venv", "venv", "dist", "build", "hf-space"}
    allowed_suffixes = {".py", ".toml", ".yaml", ".yml", ".json", ".env", ".ini", ".service", ".md"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes or any(part in skip_dirs for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in patterns:
            if pattern.search(text):
                findings.append(f"{path}: {pattern.pattern}")
    return findings

def scan_file(path: Path, patterns=ACTIVE_PATTERNS) -> list[str]:
    try: text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError: return []
    return [f"{path}: {pattern.pattern}" for pattern in patterns if pattern.search(text)]


def scan_active(root: str | Path = ".") -> list[str]:
    """Scan executable/configuration surfaces and explicit root manifests."""
    root = Path(root)
    findings: list[str] = []
    for area in ("src", "scripts", "deploy", "config"):
        target = root / area
        if target.exists():
            findings.extend(scan(target))
    for name in ("manifest.json", "manifest.overlay.json", ".env", ".env.production"):
        target = root / name
        if target.exists():
            findings.extend(scan_file(target))
    return sorted(set(findings))


def scan_documentation(root: str | Path = ".") -> list[str]:
    """Report examples in docs separately; never treats them as active config."""
    root = Path(root)
    findings: list[str] = []
    for path in root.rglob("*.md"):
        if any(part in {".git", ".pytest_cache", "data", "node_modules"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(pattern.search(text) for pattern in PATTERNS):
            findings.append(str(path))
    return sorted(set(findings))


def main() -> int:
    findings = scan_active()
    if findings:
        print("SAFETY_FREEZE_FAIL")
        print("\n".join(findings))
        return 1
    print("SAFETY_FREEZE_PASS")
    docs = scan_documentation()
    if docs:
        print(f"DOCUMENTATION_EXAMPLES_REPORTED={len(docs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
