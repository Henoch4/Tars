from src.gta_external_safety import (parse_goplus, parse_rugcheck, query_provider,
                                     GoplusSolanaClient, ExternalSafetySignal,
                                     combine_external_signals, RugcheckSafetyClient)


def test_goplus_explicit_zero_tax_is_secondary_ok():
    result = parse_goplus({"result": {"is_honeypot": "0", "sell_tax": "0"}})
    assert result.status == "ok"
    assert result.honeypot is False
    assert result.transfer_tax_pct == 0.0


def test_goplus_missing_fields_is_unknown():
    assert parse_goplus({"result": {"score": 90}}).status == "unknown"


def test_rugcheck_honeypot_risk_is_unsafe():
    result = parse_rugcheck({"risks": [{"name": "Honeypot", "level": "danger"}]})
    assert result.status == "unsafe"
    assert result.honeypot is True


def test_rugcheck_score_alone_never_proves_safety():
    result = parse_rugcheck({"score": 1, "risks": []})
    assert result.status == "unknown"


def test_provider_timeout_is_unknown_with_timestamp():
    result = query_provider(lambda: (_ for _ in ()).throw(TimeoutError()),
                            parse_goplus, source="goplus", now=100)
    assert result.status == "unknown"
    assert result.reason == "timeout"
    assert result.observed_at == 100


def test_provider_positive_result_has_freshness_and_provenance():
    result = query_provider(
        lambda: {"result": {"is_honeypot": "0", "sell_tax": "0"}},
        parse_goplus, source="goplus", now=200)
    assert result.status == "ok"
    assert result.observed_at == 200
    assert result.source == "goplus"


def test_goplus_cannot_sell_is_hard_unsafe():
    result = parse_goplus({"result": {"is_honeypot": "0", "cannot_sell_all": "1"}})
    assert result.status == "unsafe"


def test_goplus_client_auth_and_encoded_mint():
    seen = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"result": {"is_honeypot": "0", "sell_tax": "0"}}'
    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        seen["timeout"] = timeout
        return Response()
    client = GoplusSolanaClient(token="secret", endpoint="https://goplus.test", opener=opener,
                                auth_scheme="Bearer")
    payload = client.fetch("Mint/with space")
    assert payload["result"]["is_honeypot"] == "0"
    assert "contract_addresses=Mint%2Fwith+space" in seen["url"]
    assert seen["auth"] == "Bearer secret"


def test_goplus_client_missing_token_refuses():
    try:
        GoplusSolanaClient(token="").fetch("MINT")
    except RuntimeError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("missing token must fail closed")


def test_goplus_client_raw_token_auth_header():
    seen = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"code":7012,"message":"unsupported"}'
    def opener(req, timeout):
        seen["auth"] = req.headers.get("Authorization")
        return Response()
    GoplusSolanaClient(token="secret", endpoint="https://goplus.test",
                       opener=opener, auth_scheme="").fetch("MINT")
    assert seen["auth"] == "secret"


def test_unsupported_provider_response_is_unknown():
    result = parse_goplus({"code": 7012, "message": "Not fungible spl token address"})
    assert result.status == "unknown"
    assert result.reason == "missing result"


def test_rugcheck_client_uses_encoded_mint_and_returns_json():
    seen = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"risks": []}'
    def opener(req, timeout):
        seen["url"] = req.full_url
        return Response()
    payload = RugcheckSafetyClient(opener=opener).fetch("Mint/with space")
    assert payload == {"risks": []}
    assert seen["url"].endswith("Mint%2Fwith%20space/report")


def test_provider_conflict_stays_unknown():
    result = combine_external_signals(
        ExternalSafetySignal("goplus", "ok", False, 0.0, observed_at=10),
        ExternalSafetySignal("rugcheck", "unsafe", True, 15.0, observed_at=11),
    )
    assert result.status == "unknown"
    assert result.reason == "provider conflict"


def test_provider_positive_is_only_corroboration():
    result = combine_external_signals(
        ExternalSafetySignal("goplus", "ok", False, 0.0, observed_at=10),
        ExternalSafetySignal("rugcheck", "ok", False, 0.0, observed_at=12),
    )
    assert result.status == "ok"
    assert result.reason == "corroborated providers"
    assert result.observed_at == 12
