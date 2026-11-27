import base64

from src.gta_sellability import (SolanaSimulationClient, assess_round_trip,
                                  assess_simulated_round_trip, build_unsigned_swap,
                                  simulate_unsigned_round_trip)


def test_round_trip_success_measures_effective_loss():
    result = assess_round_trip(1000, 970)
    assert result.status == "passed"
    assert result.effective_tax_pct == 3.0


def test_simulated_round_trip_preserves_provenance():
    result = assess_simulated_round_trip(
        {"ok": True}, {"ok": True}, 1000, 970, provider="helius")
    assert result.status == "passed"
    assert result.effective_tax_pct == 3.0
    assert result.provenance == {
        "provider": "helius", "method": "simulateTransaction",
        "buy": "simulated", "sell": "simulated",
    }


def test_simulation_error_is_blocked_not_zero_tax():
    result = assess_simulated_round_trip(
        {"ok": True}, {"ok": False, "err": "custom program error"}, 1000, 1000)
    assert result.status == "blocked"
    assert result.effective_tax_pct is None


def test_unsigned_builder_requires_no_signing_or_broadcast():
    out = build_unsigned_swap("SOL", "TOKEN", 1000, "PUBLIC",
                              quote=lambda *a, **k: {
                                  "transaction": "AQI=", "outAmount": "900"
                              })
    assert out["transaction"] == "AQI="
    assert out["out_amount_raw"] == 900
    assert out["provenance"]["signed"] == "false"
    assert out["provenance"]["broadcast"] == "false"


def test_two_leg_unsigned_simulation_wires_buy_then_sell():
    txs = []
    def quote(input_mint, output_mint, amount, taker, slippage_bps=100):
        txs.append((input_mint, output_mint, amount))
        return {"transaction": "AQI=", "outAmount": str(800 if len(txs) == 1 else 940)}
    class Sim:
        def simulate_transaction(self, tx):
            return {"ok": True}
    result = simulate_unsigned_round_trip(
        quote_mint="SOL", token_mint="TOKEN", quote_amount_raw=1000,
        taker="PUBLIC", simulation=Sim(), quote=quote)
    assert result.status == "passed"
    assert result.effective_tax_pct == 6.0
    assert txs == [("SOL", "TOKEN", 1000), ("TOKEN", "SOL", 800)]
    assert result.provenance["signed"] == "false"


def test_unfunded_provider_failure_is_structured_unavailable():
    result = simulate_unsigned_round_trip(
        quote_mint="SOL", token_mint="TOKEN", quote_amount_raw=1000,
        taker="PUBLIC", simulation=object(),
        quote=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Insufficient funds")),
    )
    assert result.status == "unavailable"
    assert "Insufficient funds" in result.simulation_error


def test_zero_output_is_blocked():
    result = assess_round_trip(1000, 0)
    assert result.status == "blocked"
    assert result.effective_tax_pct == 100.0


def test_simulation_client_is_read_only_and_fail_closed():
    seen = {}

    def post(payload):
        seen.update(payload)
        return {"result": {"value": {"err": None, "logs": ["ok"]}}}

    client = SolanaSimulationClient("https://example.invalid", post=post)
    result = client.simulate_transaction(base64.b64encode(b"tx").decode())
    assert result["ok"] is True
    assert seen["method"] == "simulateTransaction"
    assert seen["params"][1]["sigVerify"] is False
