"""Explicit venue-position snapshot adapter for package reconciliation."""
from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable

from ..multi_leg import Package, Step

PositionRead = Callable[[Step], Awaitable[float]]
PositionKey = Callable[[Step], str]


class PositionReadError(RuntimeError):
    """The venue could not provide a trustworthy position snapshot."""


class OkxPositionReader:
    """OKX account-position adapter with explicit instrument mapping."""

    def __init__(self, cli, instrument_for_step: Callable[[Step], str],
                 key_for_step: PositionKey):
        self.cli = cli
        self.instrument_for_step = instrument_for_step
        self.key_for_step = key_for_step

    async def __call__(self, step: Step) -> float:
        inst_id = self.instrument_for_step(step)
        if not inst_id:
            raise PositionReadError(f"no instrument mapping for {step}")
        try:
            result = await self.cli.run(
                "account", "positions", "--instId", inst_id,
                use_global_flags=True,
            )
        except Exception as exc:
            raise PositionReadError(
                f"position read failed for {inst_id}: {exc}"
            ) from exc
        if isinstance(result, list):
            rows = result
        elif isinstance(result, dict) and isinstance(result.get("data"), list):
            rows = result["data"]
        else:
            raise PositionReadError(
                f"malformed position response for {inst_id}: {result!r}"
            )
        if not rows:
            return 0.0
        total = 0.0
        for row in rows:
            if not isinstance(row, dict) or "pos" not in row:
                raise PositionReadError(
                    f"position row missing pos for {inst_id}: {row!r}"
                )
            try:
                value = float(row["pos"])
            except (TypeError, ValueError) as exc:
                raise PositionReadError(
                    f"non-numeric position for {inst_id}: {row!r}"
                ) from exc
            if not math.isfinite(value):
                raise PositionReadError(f"non-finite position for {inst_id}")
            total += value
        return total


class PositionSnapshotReader:
    """Read every declared package leg without guessing instrument identity."""

    def __init__(self, read_position: PositionRead, key_for_step: PositionKey):
        self._read_position = read_position
        self._key_for_step = key_for_step

    async def read(self, package: Package) -> dict[str, float]:
        unique: dict[str, Step] = {}
        for step in package.steps:
            unique.setdefault(self._key_for_step(step), step)
        values = await asyncio.gather(*(
            self._read_position(step) for step in unique.values()
        ))
        return dict(zip(unique.keys(), values))

    @classmethod
    def from_okx(cls, cli, instrument_for_step: Callable[[Step], str],
                 key_for_step: PositionKey):
        adapter = OkxPositionReader(cli, instrument_for_step, key_for_step)
        return cls(adapter, key_for_step)
