"""Poller regression tests (scripts/meme_poll_bg.py, post-G1-failure path).

Owns three guarantees:
1. open_positions_count never reports a stale closed position as open —
   regression for the 2026-10-31 local status run where a sell_executed
   row (mint 2mPwr92SEnFKdMPGJYbCkuicCmXHtVFPse2AvFXTpump, "INGUS") read
   as "1 open position" in the operator status table.
2. The time-gate wrapper classifies death / gold / neutral hours exactly
   as meme_time_gate does.
3. The status table prints the operator-quoted rows (Poller / Interval /
   Cycle / Time gate / Next gold window / Wallet) with a wallet present
   and with the wallet unavailable.
"""
from datetime import datetime, timezone

from src.meme_exit import Position
from scripts import meme_poll_bg


def _position(**overrides) -> Position:
    base = dict(
        mint="TestMint11111111111111111111111111111111111",
        symbol="TEST",
        entry_price_usd=0.0001,
        entry_sol=0.0806,
        entry_ts=1793142485.0,
        peak_price_usd=0.00012,
        token_amount=13644.561405,
        decimals=6,
        ratchet_armed=True,
        sell_executed=False,
    )
    base.update(overrides)
    return Position(**base)


# --- regression: stale closed position must not count as open -------------


def test_open_positions_count_zero_for_stale_closed_position(monkeypatch):
    """The 2026-10-31 bug: a sell_executed row read as '1 open position(s)'."""
    monkeypatch.setattr(meme_poll_bg, "load_position",
                        lambda: _position(sell_executed=True))
    assert meme_poll_bg.open_positions_count() == 0


def test_open_positions_count_zero_for_zero_or_negative_amount(monkeypatch):
    """token_amount <= 0 means nothing is held, even unsold."""
    monkeypatch.setattr(meme_poll_bg, "load_position",
                        lambda: _position(sell_executed=False,
                                          token_amount=0.0))
    assert meme_poll_bg.open_positions_count() == 0


def test_open_positions_count_one_for_open_position(monkeypatch):
    monkeypatch.setattr(meme_poll_bg, "load_position",
                        lambda: _position(sell_executed=False))
    assert meme_poll_bg.open_positions_count() == 1


def test_open_positions_count_zero_when_no_state(monkeypatch):
    monkeypatch.setattr(meme_poll_bg, "load_position", lambda: None)
    assert meme_poll_bg.open_positions_count() == 0


def test_open_positions_count_zero_when_load_corrupt(monkeypatch):
    """A corrupt state file must read as 0, never crash the status table."""
    def _boom():
        raise ValueError("corrupt json")
    monkeypatch.setattr(meme_poll_bg, "load_position", _boom)
    assert meme_poll_bg.open_positions_count() == 0


# --- time-gate window classification ---------------------------------------


def _utc(hour: int) -> datetime:
    return datetime(2026, 10, 31, hour, 0, tzinfo=timezone.utc)


def test_window_status_death_hours():
    # Death: (0,2), (20,24) — 00:00/01:00 and 20:00-23:00 consistently
    # negative net in full-blotter analysis.
    for hour in (0, 1, 20, 21, 22, 23):
        assert "death window" in meme_poll_bg._window_status(_utc(hour)), hour


def test_window_status_gold_hours():
    # Gold: (4,7), (10,12), (18,20) — positive net per entry-hour analysis.
    for hour in (4, 5, 6, 10, 11, 18, 19):
        assert "gold window" in meme_poll_bg._window_status(_utc(hour)), hour


def test_window_status_neutral_hours():
    # Everything else: 2, 3, 7, 8, 9, 12, 13, 14, 15, 16, 17.
    for hour in (2, 3, 7, 8, 9, 12, 13, 14, 15, 16, 17):
        status = meme_poll_bg._window_status(_utc(hour))
        assert "neutral window" in status, hour


def test_next_gold_window_is_never_in_the_past():
    # 13 UTC is neutral: next gold is 18:00 today.
    label, secs = meme_poll_bg._next_gold_window(_utc(13))
    assert label == "18:00–20:00 UTC"
    assert 4.9 * 3600 < secs < 5.1 * 3600


def test_next_gold_window_wraps_to_tomorrow():
    # 19 UTC is inside the 18:00–20:00 gold window: next start wraps to
    # tomorrow's 04:00, not stay in the past.
    label, secs = meme_poll_bg._next_gold_window(_utc(19))
    assert label == "04:00–07:00 UTC"
    assert 8.9 * 3600 < secs < 9.1 * 3600  # 19:00 -> next day 04:00 = 9h


# --- status table shape (the operator-quoted format) -----------------------


def test_status_table_contains_all_quoted_rows(monkeypatch, capsys):
    monkeypatch.setattr(meme_poll_bg, "wallet_balance_sol",
                        lambda *a, **k: ("9AvGrqvwmnDEADBEEFcafe", 0.102))
    monkeypatch.setattr(meme_poll_bg, "open_positions_count", lambda: 0)

    meme_poll_bg.print_status_table("sh_test123", 5, 144, 60.0,
                                    keypath="ignored")

    out = capsys.readouterr().out
    assert "| Setting | Value |" in out
    assert "| **Poller** | Running (shell `sh_test123`) |" in out
    assert "| **Interval** | Every 60 seconds |" in out
    assert "| **Cycle** | 5 / 144 |" in out
    assert "| **Time gate** | Active — " in out
    assert "| **Next gold window** | " in out
    assert "| **Wallet** | `9AvGrqvw" in out
    assert "0.102 SOL" in out
    assert "Nothing trades automatically" in out


def test_status_table_wallet_unavailable_shape(monkeypatch, capsys):
    """Off-VPS (no keypair file) the row degrades to n/a, never fabricates."""
    monkeypatch.setattr(meme_poll_bg, "wallet_balance_sol",
                        lambda *a, **k: (None, None))
    monkeypatch.setattr(meme_poll_bg, "open_positions_count", lambda: 0)

    meme_poll_bg.print_status_table("sh_offvps", 0, 144, 60.0,
                                    keypath="ignored")

    out = capsys.readouterr().out
    assert "`unavailable` — n/a" in out


def test_status_table_reads_positions_through_counter(monkeypatch, capsys):
    """The 1-vs-0 fix must surface: stale closed position => 0 open."""
    monkeypatch.setattr(meme_poll_bg, "wallet_balance_sol",
                        lambda *a, **k: ("9AvGrqvwmnDEADBEEFcafe", 0.102))
    monkeypatch.setattr(meme_poll_bg, "open_positions_count", lambda: 0)

    meme_poll_bg.print_status_table("sh_counts", 1, 144, 60.0,
                                    keypath="ignored")

    out = capsys.readouterr().out
    assert "0 open position(s)" in out
    assert "1 open position" not in out


# --- regression: getBalance request builder --------------------------------


def test_getbalance_request_is_a_valid_post_request():
    """Regression 2026-10-31 (found on-VPS): the old site used
    __import__("urllib.request").Request, which returns the top-level
    `urllib` module — `.Request` never existed on it. The local status run
    returned before the RPC call, so the bug only fired when the keypair
    file existed (wallet present). This test would have caught it offline.
    """
    req = meme_poll_bg.getbalance_request(
        "9AvGrqvwmnDEADBEEFcafe", "https://rpc.solami.fast/sol?api_key=x")

    assert req.get_method() == "POST"
    assert req.full_url.startswith("https://rpc.solami.fast/sol?api_key=")
    assert req.headers["Content-type"] == "application/json"
    body = req.data.decode()
    assert '"method": "getBalance"' in body
    assert '"9AvGrqvwmnDEADBEEFcafe"' in body
    assert "urllib.request" not in body  # the old __import__ trap, gone


# --- regression: agent.env injection ----------------------------------------


def test_load_agent_env_parses_and_does_not_override(monkeypatch, tmp_path):
    """Regression 2026-10-31 (found on-VPS): the poller only injected
    /etc/tars/agent.env lazily inside the wallet RPC path, so on a box
    without a repo .env the Helius enrichment env never loaded and every
    screen failed-closed to zero candidates. This pins the loader: parse
    KEY=VALUE lines, skip comments/junk, never override an existing var.
    """
    env_file = tmp_path / "agent.env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "POLL_TEST_KEY_A=preset-checked\n"
        "POLL_TEST_KEY_B=quoted\n"
        "BADLINE_NO_EQUALS\n"
        "POLL_TEST_KEY_C=https://rpc.example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(meme_poll_bg, "AGENT_ENV", str(env_file))
    monkeypatch.setattr(meme_poll_bg, "_load_agent_env_called", True)
    # deterministic start (names chosen to not exist in any ambient env)
    monkeypatch.delenv("POLL_TEST_KEY_A", raising=False)
    monkeypatch.delenv("POLL_TEST_KEY_B", raising=False)
    monkeypatch.delenv("POLL_TEST_KEY_C", raising=False)
    # pre-existing value must NOT be overridden
    monkeypatch.setenv("POLL_TEST_KEY_A", "preset-value")

    meme_poll_bg._load_agent_env()

    import os
    assert os.environ["POLL_TEST_KEY_A"] == "preset-value"
    assert os.environ["POLL_TEST_KEY_B"] == "quoted"
    assert os.environ["POLL_TEST_KEY_C"] == "https://rpc.example"
