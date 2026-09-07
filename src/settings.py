"""Single source of truth for wiring config (environment variables).

Phase A of TODO.md Phase 5's top item: every wiring knob is declared
exactly once, with the repo's current default, and validated at boot so a
typo'd env var fails in seconds — not mid-cycle at runtime.

Scope discipline (per the TODO design):
- This module owns WIRING only: hosts, keys, limits, floors, modes.
- ``config/profiles.yaml`` stays POLICY (curator territory) — untouched.
- ``MOOVE_API_KEY`` / ``VALIDATION_RETURNS_PATH`` / vault addresses are
  read dynamically at request time (tests + key rotation depend on it);
  they are declared here for the single table but consumed via os.getenv.
- ``CURATOR_*`` overrides, scheduler/rpc/vault reads still consume
  os.getenv directly (Phase B); declaring them here already subjects
  them to boot validation.

Bool semantics are preserved EXACTLY per key (the repo uses two
flavors): ``== "true"`` (DRY_RUN, ALLOW_LIVE, …) vs ``in ("1",
"true", "yes")`` (REGIME_THROTTLE, MOOVE_GATE_HIRE). An empty value
means "unset" → default (matches .env.example's empty-override
convention); previously an empty numeric crashed at import, now it
falls back — strictly more forgiving, never stricter, except the
documented gt=0 floors and the ALLOW_LIVE interlock below.
"""
from __future__ import annotations

import logging
import os
import pathlib
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """Minimal .env loader (no new dependency).

    Moved here from src/exchange.py (which re-imports it — idempotent).
    Python does NOT auto-load .env on Windows — settings would miss
    everything in the repo's .env file without this. Parses KEY=VALUE
    lines from the repo-root .env and sets only keys that aren't already
    in the environment (real env vars always win). Never logs values.
    """
    try:
        root = pathlib.Path(__file__).resolve().parent.parent
        env_path = root / ".env"
        if not env_path.is_file():
            return
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as e:  # a broken .env must never crash the app
        logger.warning(f".env loader skipped: {e}")


_load_dotenv()


def _empty_to_none(v: Any) -> Any:
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _flag_strict(v: Any) -> bool:
    """Repo `== "true"` flavor (DRY_RUN, ALLOW_LIVE, OKX_DEMO, ...)."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


def _flag_lenient(v: Any) -> bool:
    """Repo `in ("1", "true", "yes")` flavor (REGIME_THROTTLE, ...)."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes")


StrictFlag = Annotated[bool, BeforeValidator(_flag_strict)]
LenientFlag = Annotated[bool, BeforeValidator(_flag_lenient)]
OptStr = Annotated[str | None, BeforeValidator(_empty_to_none)]
PositiveFloat = Annotated[float, Field(gt=0)]
PositiveInt = Annotated[int, Field(gt=0)]


class Settings(BaseSettings):
    """All 49 wiring knobs. Defaults == the repo's current behavior."""

    # env_ignore_empty: an empty value means "unset" → default (matches
    # .env.example's empty-override convention; previously an empty
    # numeric crashed at import, now it falls back).
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    # --- Trading mode ---
    dry_run: StrictFlag = True
    allow_live: StrictFlag = False
    exchange: str = "okx"
    agent_id: str = "autonomous-trader-001"

    # --- Risk parameters (non-overridable gate inputs) ---
    max_position_usd: PositiveFloat = 5000
    max_daily_loss_usd: PositiveFloat = 500
    max_daily_trades: PositiveInt = 10
    max_daily_volume_usd: PositiveFloat = 50000
    max_leverage: PositiveFloat = 5.0
    min_confidence_bps: PositiveInt = 7000
    max_price_age_seconds: PositiveFloat = 60
    loss_cooldown_minutes: PositiveFloat = 30
    drawdown_window_days: PositiveInt = 3
    drawdown_loss_mult: PositiveFloat = 2.0

    # --- Sizing (fractional Kelly) ---
    sizing_mode: str = "kelly"
    kelly_fraction: PositiveFloat = 0.5

    # --- Regime throttle + filter ---
    regime_throttle: LenientFlag = False
    regime_band_pct: PositiveFloat = 5.0
    regime_size_scale: PositiveFloat = 0.8
    regime_filter_window: PositiveInt = 50

    # --- Funding arb + multi-leg ---
    funding_arb_min_rate: PositiveFloat = 0.001
    max_concurrent_packages: PositiveInt = 3
    multi_leg_state_dir: str = "data/multi_leg_state"

    # --- Auth / exchange credentials (empty = unset) ---
    agent_api_token: str = ""
    okx_base_url: str = ""
    okx_demo: StrictFlag = True
    okx_profile: OptStr = None
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: StrictFlag = False
    binance_base_url: OptStr = None

    # --- On-chain audit trail (X Layer) ---
    xlayer_rpc_url: str = ""
    xlayer_rpc_url_fallback: str = ""
    xlayer_chain_id: PositiveInt = 1952
    audit_contract_address: str = ""
    agent_wallet_private_key: str = ""
    vault_contract_address: str = ""

    # --- Alerting / audit log / integrity ---
    alert_webhook_url: str = ""
    alert_cooldown_seconds: PositiveInt = 300
    audit_log_path: str = "audit_log.jsonl"
    data_staleness_seconds: PositiveFloat = 30

    # --- Curator per-knob overrides (Phase B consumers; None = profile) ---
    curator_position_size_multiplier: OptStr = None
    curator_confidence_floor_bps: OptStr = None
    curator_max_leverage: OptStr = None

    # --- x402 pricing ---
    pay_to_address: str = ""
    x402_max_usd_per_call: PositiveFloat = 5.00
    x402_premium_per_min: PositiveInt = 10
    x402_micro_per_min: PositiveInt = 120

    # --- Moove billing (declared; consumed dynamically per-request) ---
    moove_api_key: str = ""
    moove_gate_hire: LenientFlag = False

    # --- Validation gate data (declared; consumed dynamically) ---
    validation_returns_path: str = ""

    # --- Scheduler (declared; scheduler.py consumes directly, Phase B) ---
    loop_heartbeat_path: str = "data/loop_heartbeat.json"

    # --- Durable risk counters (declared; risk_gate consumes directly) ---
    risk_state_path: OptStr = None

    @model_validator(mode="after")
    def _allow_live_requires_deliberate_dry_run(self) -> "Settings":
        """ALLOW_LIVE=true is only meaningful beside an explicit DRY_RUN.

        Accepting a defaulted DRY_RUN here would let a half-configured
        deploy drift into live audits without anyone deciding. .env-file
        values count as explicit (the loader runs before this check).
        """
        if self.allow_live and "DRY_RUN" not in os.environ:
            raise ValueError(
                "ALLOW_LIVE=true requires DRY_RUN to be set explicitly "
                "(true or false) — refusing to run on a defaulted value."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide validated settings. Use refresh_settings() in tests
    after monkeypatching the environment."""
    return Settings()


def refresh_settings() -> Settings:
    """Drop the cache and re-read the environment (tests only)."""
    get_settings.cache_clear()
    return get_settings()
