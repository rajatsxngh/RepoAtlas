"""Hard limits on spending (DEP-5).

``SpendGuard`` caps what all LLM calls of a deployment may cost per UTC day. The day's total
comes from the trace store (the cost of ``llm`` spans started since midnight UTC), so every
process that writes traces to the same database counts against the same cap. The total is
cached for ``ttl_s`` (30 seconds by default), so a check costs a query at most that often.

``LLMClient`` checks the guard before every request it sends, repair calls included, and raises
``SpendCapReached`` without sending. Answer systems turn that into a ``budget`` refusal.

Two limits of this design, both small next to the cap:

- Spans are written when their trace ends, so calls in flight are not counted yet.
- Between refreshes the guard adds what this process spent (``record``), but not what other
  processes spent; they are seen at the next refresh.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from repoatlas.config import Settings
from repoatlas.llm.types import SpendCapReached
from repoatlas.logging import get_logger

DEFAULT_TTL_S = 30.0

_SPENT_SINCE = text(
    "SELECT coalesce(sum(cost_usd), 0) FROM spans WHERE kind = 'llm' AND started_at >= :since"
)


__all__ = ["DEFAULT_TTL_S", "SpendCapReached", "SpendGuard", "start_of_day", "utc_now"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def start_of_day(moment: datetime) -> datetime:
    """Midnight UTC of ``moment``'s day."""
    return moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class SpendGuard:
    """A daily cap in dollars on LLM spending, read from the ``spans`` table."""

    def __init__(
        self,
        engine: Engine,
        cap_usd: float,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        if cap_usd < 0:
            raise ValueError("The daily spend cap can't be negative.")
        self.engine = engine
        self.cap_usd = cap_usd
        self.ttl_s = ttl_s
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._day: datetime | None = None
        self._stored = 0.0  # the day's total at the last refresh
        self._pending = 0.0  # spent by this process since then
        self._read_at: float | None = None
        self._alerted: datetime | None = None  # the day an alert was last logged for

    @classmethod
    def from_settings(cls, settings: Settings, engine: Engine) -> SpendGuard | None:
        """The guard for the settings' cap, or ``None`` when no cap is set."""
        if settings.daily_spend_cap_usd is None:
            return None
        return cls(engine, settings.daily_spend_cap_usd)

    def spent_today(self) -> float:
        """Dollars spent on LLM calls since midnight UTC (at most ``ttl_s`` old)."""
        with self._lock:
            return self._spent()

    def record(self, cost_usd: float) -> None:
        """Count a call this process just paid for, until the next refresh sees its span."""
        if cost_usd > 0:
            with self._lock:
                self._pending += cost_usd

    def check(self) -> None:
        """Raise ``SpendCapReached`` when the day's spending has reached the cap."""
        with self._lock:
            spent = self._spent()
            if spent < self.cap_usd:
                return
            if self._alerted != self._day:
                self._alerted = self._day
                # A log stream that can't be written (closed) must not let a call through.
                with contextlib.suppress(ValueError, OSError):
                    get_logger(__name__).error(
                        "daily spend cap reached",
                        spent_usd=round(spent, 4),
                        cap_usd=self.cap_usd,
                        day=None if self._day is None else self._day.date().isoformat(),
                    )
        raise SpendCapReached(spent, self.cap_usd)

    def _spent(self) -> float:
        day = start_of_day(self._now())
        tick = self._clock()
        stale = self._read_at is None or tick - self._read_at >= self.ttl_s
        if stale or day != self._day:
            with self.engine.connect() as connection:
                self._stored = float(connection.execute(_SPENT_SINCE, {"since": day}).scalar_one())
            self._day = day
            self._pending = 0.0
            self._read_at = tick
        return self._stored + self._pending
