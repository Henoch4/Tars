import pytest
from src.gta_stateful_replay import replay_round_trip, validate_snapshot


class Backend:
    def __init__(self, buy=123, sell=900_000, fail_buy=False, fail_sell=False):
        self.buy, self.sell, self.fail_buy, self.fail_sell = buy, sell, fail_buy, fail_sell
    def apply_buy(self, _tx):
        if self.fail_buy: return {"ok": False, "slot": 10}
        return {"ok": True, "token_amount_raw": self.buy, "slot": 10}
    def build_sell(self, amount):
        assert amount == self.buy
        return "sell"
    def simulate_sell(self, _tx):
        if self.fail_sell: return {"ok": False, "slot": 11}
        return {"ok": True, "quote_output_raw": self.sell, "slot": 11}


def test_successful_stateful_round_trip_is_simulation_only():
    r = replay_round_trip(backend=Backend(), buy_transaction="buy", quote_amount_raw=1_000_000)
    assert r.status == "passed"
    assert r.effective_loss_pct == 10.0
    assert r.provenance["simulation_only"] is True


def test_missing_token_account_is_unknown():
    r = replay_round_trip(backend=Backend(buy=None), buy_transaction="buy", quote_amount_raw=1_000_000)
    assert r.status == "unavailable"


def test_failed_buy_and_sell_fail_closed():
    assert replay_round_trip(backend=Backend(fail_buy=True), buy_transaction="buy", quote_amount_raw=1_000_000).status == "blocked"
    assert replay_round_trip(backend=Backend(fail_sell=True), buy_transaction="buy", quote_amount_raw=1_000_000).status == "blocked"


def test_malformed_state_does_not_pass():
    r = replay_round_trip(backend=Backend(buy="123"), buy_transaction="buy", quote_amount_raw=1_000_000)
    assert r.status == "unavailable"


def test_missing_buy_transaction_is_unavailable():
    assert replay_round_trip(backend=Backend(), buy_transaction="", quote_amount_raw=1_000_000).status == "unavailable"


def _snapshot(**extra):
    row = {"source_identity": "fixture", "slot": 10, "ledger_slot": 0,
           "provenance_mode": "synthetic_replay", "block_height": 20,
           "commitment": "finalized", "observed_at": 100,
           "runtime_version": "test", "program_hashes": {"token": "abc"},
           "runtime_dependency": {"runtime_provided": True, "loader_identity": "native",
                                  "validator_version": "test", "compatibility": True},
           "mint": "M", "pool": "P", "programs": ["token"],
           "accounts": ["mint", "pool"]}
    row.update(extra)
    return row


def test_snapshot_requires_complete_state():
    with pytest.raises(RuntimeError, match="incomplete"):
        validate_snapshot({})


def test_snapshot_rejects_stale_or_wrong_runtime():
    with pytest.raises(RuntimeError, match="stale"):
        validate_snapshot(_snapshot(), now=1100)
    with pytest.raises(RuntimeError, match="version"):
        validate_snapshot(_snapshot(runtime_version="old"), now=100, runtime_version="new")


def test_snapshot_requires_finalized_provenance():
    with pytest.raises(RuntimeError, match="finalized"):
        validate_snapshot(_snapshot(commitment="confirmed"), now=100)
    with pytest.raises(RuntimeError, match="hashes"):
        validate_snapshot(_snapshot(program_hashes={}), now=100)


def test_snapshot_rejects_missing_programs_accounts_and_conflicts():
    for field in ("programs", "accounts"):
        with pytest.raises(RuntimeError):
            validate_snapshot(_snapshot(**{field: []}), now=100)
    with pytest.raises(RuntimeError, match="conflict"):
        validate_snapshot(_snapshot(conflict=True), now=100)
