"""Release gate (Ask 3.3): one script, all scriptable pre-ship checks.

Gates (all must pass; skips are printed, never silent):
  1. secrets      — gitleaks if installed, else SKIP (visible, not green)
  2. pytest       — full suite (slow = correct for a ship gate)
  3. manifest     — check_manifest() doctor must be healthy
  4. artifacts    — hermetic round-trip smoke over every durable format:
                     positions save/load, package save/recover, heartbeat
                     beat/get, audit write/seal/rolloff (tmp dirs only)
Manual remainder is PRINTED, not checked: docs review, durable backup,
VPS smoke. "Looks good to me" is not a gate; neither is this script's
silence — read its output.

Usage: py -3 scripts/check_release.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def gate_secrets():
    if shutil.which("gitleaks") is None:
        return SKIP, "gitleaks not installed — run CI secret-scan before ship"
    r = subprocess.run(
        ["gitleaks", "detect", "--source", REPO, "--no-git", "-v"],
        capture_output=True, text=True, cwd=REPO)
    if r.returncode == 0:
        return PASS, "gitleaks clean"
    return FAIL, (r.stdout + r.stderr)[:500]


def gate_pytest():
    py = sys.executable
    r = subprocess.run([py, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, cwd=REPO)
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    if r.returncode == 0:
        return PASS, " | ".join(tail)[-160:]
    return FAIL, " | ".join(tail)[-300:]


def gate_manifest():
    from scripts.check_manifest import check_manifest
    problems = check_manifest()
    if not problems:
        return PASS, "manifest doctor healthy"
    return FAIL, "; ".join(problems)[:300]


def gate_artifacts():
    """Hermetic round-trips over every durable format (tmp only)."""
    notes = []
    try:
        from src.meme_loop import SatelliteLoop
        with tempfile.TemporaryDirectory() as d:
            loop = SatelliteLoop(blotter_path=os.path.join(d, "b.jsonl"),
                                 positions_path=os.path.join(d, "p.json"),
                                 entry_size_usd=25.0, max_session_usd=100.0)
            from src.meme_satellite import TokenSnapshot
            snap = TokenSnapshot(mint="M", top10_hold_pct=20.0, holder_count=25,
                                 creator_hold_pct=3.0, lp_locked=True,
                                 mint_authority_renounced=True,
                                 freeze_authority_renounced=True,
                                 age_s=100000.0, liquidity_usd=100000.0,
                                 buy_tax_pct=1.0, sell_tax_pct=2.0,
                                 creator_past_rugs=0, graduated=True)
            assert loop.try_enter(snap, price_usd=1.0, liquidity_usd=50000.0,
                                  volume_24h_usd=10000.0) is True
            loop2 = SatelliteLoop(blotter_path=os.path.join(d, "b.jsonl"),
                                  positions_path=os.path.join(d, "p.json"),
                                  entry_size_usd=25.0, max_session_usd=100.0)
            assert "M" in loop2.open_positions
        notes.append("positions save/load ok")
    except Exception as e:
        return FAIL, f"positions round-trip: {type(e).__name__}: {e}"[:200]

    try:
        from src.multi_leg import MultiLegExecutionManager
        with tempfile.TemporaryDirectory() as d:
            m1 = MultiLegExecutionManager(persist_dir=d)
            from src.multi_leg import Package, PackageState, Step
            pkg = Package(id=7, steps=[Step(venue="v", action="buy",
                                            asset="BTC", amount_ratio=1.0,
                                            max_slippage_pct=1.0)],
                          notional=1000.0, state=PackageState.PENDING_FILL)
            m1._save_package(pkg)
            m2 = MultiLegExecutionManager(persist_dir=d)
            assert m2.open_package_count() >= 1
        notes.append("package save/recover ok")
    except Exception as e:
        return FAIL, f"package round-trip: {type(e).__name__}: {e}"[:200]

    try:
        from src import metrics
        metrics.reset()
        metrics.beat("gate", metrics.OUTCOME_NO_TRADES, {}, 0)
        assert metrics.get_heartbeat()["cycle_id"] == "gate"
        assert metrics.check_loop_stall(3600.0) is False
        metrics.reset()
        notes.append("heartbeat beat/check ok")
    except Exception as e:
        return FAIL, f"heartbeat round-trip: {type(e).__name__}: {e}"[:200]

    try:
        from src.audit_trail import AuditLog
        with tempfile.TemporaryDirectory() as d:
            log = AuditLog(os.path.join(d, "a.jsonl"))
            log.begin_cycle("c1")
            log.write("observation", {"i": 0})
            seal = log.seal_cycle()
            assert seal["count"] == 1 and seal["root"]
            rep = log.rolloff(keep_cycles=1)
            assert rep["seals"] == 1
        notes.append("audit write/seal/rolloff ok")
    except Exception as e:
        return FAIL, f"audit round-trip: {type(e).__name__}: {e}"[:200]

    return PASS, "; ".join(notes)


MANUAL = """MANUAL (not scriptable — do not ship without):
  [ ] docs reviewed (TODO statuses match code; no secret in diff: git diff)
  [ ] VPS: backup data/ + /etc/tars, deploy, smoke curl /health + --status
  [ ] observer isolation re-verified if loop files changed"""


def main():
    results = []
    for name, fn in (("secrets", gate_secrets),
                     ("pytest", gate_pytest),
                     ("manifest", gate_manifest),
                     ("artifacts", gate_artifacts)):
        try:
            status, note = fn()
        except Exception as e:
            status, note = FAIL, f"gate crashed: {type(e).__name__}: {e}"[:200]
        results.append((name, status, note))
        print(f"[{status}] {name}: {note}")
    print()
    print(MANUAL)
    failed = [n for n, s, _ in results if s == FAIL]
    if failed:
        print(f"\nGATE CLOSED: {', '.join(failed)}")
        return 1
    skipped = [n for n, s, _ in results if s == SKIP]
    if skipped:
        print(f"\nGATE OPEN WITH SKIPS: {', '.join(skipped)} — clear before ship")
        return 2
    print("\nGATE OPEN: all scriptable checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
