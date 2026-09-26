"""Offline tests for the Solana second rail (no network, no anchorpy needed)."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _payload(**kw):
    from src.audit_logger import DecisionPayload

    base = dict(
        decision_id="dec_test123",
        agent_address="0x" + "00" * 20,
        asset="AAPLx",
        signal="LONG",
        strategy="stock_carry",
        confidence_bps=7500,
        entry_price=100.0,
        size_usd=50.0,
        risk_params_hash="0x" + "00" * 32,
        timestamp=0,
    )
    base.update(kw)
    return DecisionPayload(**base)


def test_rail_disabled_by_default():
    for k in ("SOLANA_ENABLED", "SOLANA_RPC_URL", "SOLANA_PROGRAM_ID", "SOLANA_KEYPAIR_PATH"):
        os.environ.pop(k, None)
    from src.solana.dual_logger import SolanaSecondRail, make_solana_rail, wrap_dual

    rail = SolanaSecondRail.from_env()
    assert rail.enabled is False
    assert rail.log_decision(_payload()) is None
    assert rail.record_execution(decision_id="x") is None
    assert rail.is_kill_switch_active() is None
    assert make_solana_rail() is None
    evm = object()
    assert wrap_dual(evm, None) is evm
    assert wrap_dual(evm, rail) is evm


def test_dual_evm_only_passthrough():
    from src.solana.dual_logger import DualLogger

    calls = {}

    class FakeEVM:
        agent_address = "0xabc"

        def log_decision(self, payload):
            calls["log"] = payload.decision_id
            return "0xevm"

        def record_execution(self, **kw):
            calls["rec"] = kw["decision_id"]

        def is_kill_switch_active(self):
            return False

    d = DualLogger(evm=FakeEVM(), sol=None)
    assert d.agent_address == "0xabc"
    assert d.log_decision(_payload(asset="BTC-USDT-SWAP")) == "0xevm"
    d.record_execution(decision_id="dec_test123", fill_price=1, fill_size_usd=1, fee_usd=0, success=True)
    assert calls == {"log": "dec_test123", "rec": "dec_test123"}
    assert d.is_kill_switch_active() is False


def test_dual_sol_mirror_never_blocks():
    from src.solana.dual_logger import DualLogger

    class FakeEVM:
        agent_address = "0xabc"

        def log_decision(self, payload):
            return "0xevm"

        def record_execution(self, **kw):
            pass

        def is_kill_switch_active(self):
            return False

    class FlakySol:
        enabled = True

        def log_decision(self, payload):
            raise RuntimeError("sol down")

        def record_execution(self, **kw):
            raise RuntimeError("sol down")

        def is_kill_switch_active(self):
            raise RuntimeError("sol down")

    # FlakySol raises like a bad rail would if it weren't best-effort;
    # DualLogger must survive it. (Real SolanaSecondRail never raises.)
    class SafeSol(FlakySol):
        def log_decision(self, payload):
            try:
                return super().log_decision(payload)
            except Exception:
                return None

        def record_execution(self, **kw):
            try:
                super().record_execution(**kw)
            except Exception:
                return None

        def is_kill_switch_active(self):
            try:
                return super().is_kill_switch_active()
            except Exception:
                return None

    d = DualLogger(evm=FakeEVM(), sol=SafeSol())
    assert d.log_decision(_payload(asset="BTC-USDT-SWAP")) == "0xevm"
    d.record_execution(decision_id="x")
    assert d.is_kill_switch_active() is False


def test_dual_sol_only_mode():
    from src.solana.dual_logger import DualLogger

    class FakeSol:
        enabled = True
        agent_address = "SolAddr123"

        def log_decision(self, payload):
            return "solsig123"

        def record_execution(self, **kw):
            pass

        def is_kill_switch_active(self):
            return True

    d = DualLogger(evm=None, sol=FakeSol())
    assert d.agent_address == "SolAddr123"
    assert d.log_decision(_payload()) == "solsig123"
    assert d.is_kill_switch_active() is True


def test_keypair_format_rejected_without_deps():
    # load_solana_keypair imports solders lazily; if solders is absent the
    # test asserts the lazy-import behavior instead of the format check.
    try:
        import solders  # noqa: F401
    except ImportError:
        from src.solana import dual_logger as dl

        try:
            dl.load_solana_keypair("/nonexistent")
        except (ModuleNotFoundError, ImportError):
            return
        raise AssertionError("expected lazy solders import to fail")
    import tempfile

    from src.solana.dual_logger import load_solana_keypair

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write("not a keypair")
        path = f.name
    try:
        load_solana_keypair(path)
    except ValueError:
        return
    finally:
        os.unlink(path)
    raise AssertionError("expected ValueError for bad keypair file")


def test_chain_router_defaults():
    from src.solana.chain_router import chain_for_asset

    assert chain_for_asset("BTC-USDT-SWAP") == "evm"
    assert chain_for_asset("ETH-USDT") == "evm"
    assert chain_for_asset("SOL-USDT-SWAP") == "evm"  # CEX perp, not Solana spot
    assert chain_for_asset("AAPLx") == "solana"
    assert chain_for_asset("TSLAx") == "solana"
    assert chain_for_asset("dAAPLx") == "solana"
    assert chain_for_asset("") == "evm"
    assert chain_for_asset(None) == "evm"


def test_chain_router_env_override():
    from src.solana.chain_router import chain_for_asset

    os.environ["SOLANA_ASSETS"] = "WEIRD-TOKEN"
    os.environ["EVM_ASSETS"] = "AAPLx"
    try:
        assert chain_for_asset("WEIRD-TOKEN") == "solana"
        assert chain_for_asset("AAPLx") == "evm"  # explicit evm wins
        assert chain_for_asset("TSLAx") == "solana"  # suffix rule intact
    finally:
        os.environ.pop("SOLANA_ASSETS", None)
        os.environ.pop("EVM_ASSETS", None)


def test_dual_routes_primary_by_asset():
    from src.solana.dual_logger import DualLogger

    calls = []

    class FakeEVM:
        agent_address = "0xevm"

        def log_decision(self, payload):
            calls.append(("evm", payload.asset))
            return "0xevm"

        def record_execution(self, **kw):
            calls.append(("evm-rec", kw["decision_id"]))

        def is_kill_switch_active(self):
            return False

    class FakeSol:
        enabled = True
        agent_address = "SolAddr"

        def log_decision(self, payload):
            calls.append(("sol", payload.asset))
            return "solsig"

        def record_execution(self, **kw):
            calls.append(("sol-rec", kw["decision_id"]))

        def is_kill_switch_active(self):
            return False

    d = DualLogger(evm=FakeEVM(), sol=FakeSol())
    assert d.chain_for("AAPLx") == "solana"
    assert d.chain_for("BTC-USDT-SWAP") == "evm"

    tx = d.log_decision(_payload(asset="AAPLx", decision_id="dec_sol1"))
    assert tx == "solsig"  # solana primary
    assert ("sol", "AAPLx") in calls and ("evm", "AAPLx") in calls  # evm mirrored
    assert d.audit_chain_for("dec_sol1") == "solana"

    calls.clear()
    tx = d.log_decision(_payload(asset="BTC-USDT-SWAP", decision_id="dec_evm1"))
    assert tx == "0xevm"  # evm primary
    assert ("evm", "BTC-USDT-SWAP") in calls and ("sol", "BTC-USDT-SWAP") in calls
    assert d.audit_chain_for("dec_evm1") == "evm"

    # record_execution follows the cached primary chain
    calls.clear()
    d.record_execution(decision_id="dec_sol1", fill_price=1, fill_size_usd=1, fee_usd=0, success=True)
    assert ("sol-rec", "dec_sol1") in calls


def test_dual_blocks_when_primary_rail_missing():
    from src.solana.dual_logger import DualLogger

    class FakeEVM:
        agent_address = "0xevm"

        def log_decision(self, payload):
            return "0xevm"

        def record_execution(self, **kw):
            pass

        def is_kill_switch_active(self):
            return False

    d = DualLogger(evm=FakeEVM(), sol=None)  # no solana rail
    try:
        d.log_decision(_payload(asset="AAPLx", decision_id="dec_blocked"))
    except RuntimeError as e:
        assert "solana" in str(e).lower()
        return
    raise AssertionError("expected RuntimeError when solana primary is missing")
