"""Durable breaker state: the halt must outlive the process that tripped it.

`SatelliteBreakers` decides (pure, no IO — see its module note). This module
is the store: one JSON file beside the blotter recording every material
state transition, restored at boot. Design constraints, all pinned by test:

  - Atomic write (tmp + os.replace): a crash mid-write can never leave a
    half-state file.
  - A save failure must be LOUD in the runner's output — a halt that
    silently failed to persist can evaporate on restart, which is precisely
    the failure this layer exists to prevent.
  - An UNREADABLE state file is treated as HALTED (fail-closed, set by the
    runner's loader, not here): absence of evidence about a safety state is
    not evidence of "no halt". An operator reset rewrites the file and
    resumes entries; nothing auto-clears it.

File shape: {"ts": <last-write unix ts>, **SatelliteBreakers.state()}.
Unknown/extra keys are ignored by apply_state, so the file remains readable
by future schema additions without migration.
"""
from __future__ import annotations

import json
import os

from .satellite_breakers import SatelliteBreakers


def read_breaker_state(path: str) -> tuple[dict | None, str | None]:
    """(state_dict, error_note). Missing file -> (None, None): first boot,
    no prior state to restore — NOT an error. Unreadable/corrupt ->
    (None, note): the caller's fail-closed path turns this into a halt.
    A structurally-clean dict returns (dict, None) even if fields are
    garbage values — apply_state degrades those to fresh defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"unreadable: {type(e).__name__}"
    if not isinstance(state, dict):
        return None, f"unreadable: top-level {type(state).__name__}"
    return state, None


def write_breaker_state(path: str, breaker: SatelliteBreakers,
                        ts: float) -> None:
    """Atomically persist the breaker's full state. Raises on OS failure —
    callers must be louder than the failure (see banner in the runner)."""
    path = str(path)
    dir_ = os.path.dirname(path)
    if dir_:
        os.makedirs(dir_, exist_ok=True)
    payload = {"ts": round(float(ts), 3), **breaker.state()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)
