"""Account-level API response times and request counts (in memory).

* **Last response time** is the most recent request, whatever its outcome.
* **Average** covers the last :data:`~.const.LATENCY_WINDOW` requests that got
  an HTTP response. Timeouts and connection failures stay in the window but
  are excluded from the average and counted as ``failed_requests``.
* **Requests today** counts every HTTP request since local midnight, broken
  down by operation and outcome. It resets at midnight and on restart.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .api import NO_RESPONSE_OUTCOMES, RequestRecord
from .const import LATENCY_WINDOW


@dataclass(frozen=True)
class Sample:
    """One timed request (no bodies, tokens, ids or names)."""

    operation: str
    outcome: str
    http_status: int | None
    duration_ms: float
    measured_at: datetime

    @property
    def responded(self) -> bool:
        """True when an HTTP response arrived."""
        return self.outcome not in NO_RESPONSE_OUTCOMES

    @classmethod
    def from_record(cls, rec: RequestRecord) -> Sample:
        """Build from a client record."""
        return cls(
            operation=rec.operation,
            outcome=rec.outcome,
            http_status=rec.http_status,
            duration_ms=max(0.0, rec.duration * 1000),
            measured_at=rec.started + timedelta(seconds=max(0.0, rec.duration)),
        )

    def as_dict(self) -> dict[str, Any]:
        """Diagnostics view."""
        return {
            "operation": self.operation,
            "outcome": self.outcome,
            "http_status": self.http_status,
            "duration_ms": round(self.duration_ms),
            "measured_at": self.measured_at.isoformat(),
        }


class ApiStats:
    """Rolling latency window plus today's request counters."""

    def __init__(self, window: int = LATENCY_WINDOW) -> None:
        """Initialize empty."""
        self._samples: deque[Sample] = deque(maxlen=window)
        self.day: date | None = None
        self.by_operation: Counter[str] = Counter()
        self.by_outcome: Counter[str] = Counter()

    def add(self, rec: RequestRecord, today: date) -> Sample:
        """Record one request."""
        sample = Sample.from_record(rec)
        self._samples.append(sample)
        if self.day != today:
            self.day = today
            self.by_operation.clear()
            self.by_outcome.clear()
        self.by_operation[rec.operation.split("/")[0]] += 1
        self.by_outcome[rec.outcome] += 1
        return sample

    def roll(self, today: date) -> None:
        """Reset the daily counters when the local date changes."""
        if self.day is not None and self.day != today:
            self.day = today
            self.by_operation.clear()
            self.by_outcome.clear()

    @property
    def last(self) -> Sample | None:
        """Most recent request."""
        return self._samples[-1] if self._samples else None

    def _responded(self) -> list[float]:
        return [s.duration_ms for s in self._samples if s.responded]

    @property
    def last_ms(self) -> int | None:
        """Duration of the latest request (ms)."""
        return round(self.last.duration_ms) if self.last else None

    @property
    def average_ms(self) -> int | None:
        """Mean over responded requests in the window (ms)."""
        values = self._responded()
        return round(sum(values) / len(values)) if values else None

    @property
    def requests_today(self) -> int:
        """Requests since local midnight (or since start)."""
        return sum(self.by_operation.values())

    def last_attributes(self) -> dict[str, Any]:
        """Attributes for the last-response-time sensor."""
        last = self.last
        if last is None:
            return {}
        return {
            "operation": last.operation,
            "outcome": last.outcome,
            "http_status": last.http_status,
            "measured_at": last.measured_at.isoformat(),
        }

    def average_attributes(self) -> dict[str, Any]:
        """Attributes for the average sensor."""
        values = self._responded()
        return {
            "window_requests": self._samples.maxlen,
            "sample_count": len(values),
            "failed_requests": sum(1 for s in self._samples if not s.responded),
            "max_ms": round(max(values)) if values else None,
            "min_ms": round(min(values)) if values else None,
        }

    def usage_attributes(self) -> dict[str, Any]:
        """Attributes for the requests-today sensor."""
        return {
            "date": self.day.isoformat() if self.day else None,
            "by_operation": dict(sorted(self.by_operation.items())),
            "by_outcome": dict(sorted(self.by_outcome.items())),
        }

    def snapshot(self) -> dict[str, Any]:
        """Diagnostics view."""
        return {
            "last_ms": self.last_ms,
            "average_ms": self.average_ms,
            **self.average_attributes(),
            "requests_today": self.requests_today,
            **self.usage_attributes(),
            "samples": [s.as_dict() for s in self._samples],
        }
