"""Per-camera state: confirmed values, pending commands, read ordering.

The rules (docs/DESIGN.md section 4):

* ``confirmed`` only ever comes from a Wyze property read that was accepted.
* A read is discarded when its request started before the camera's latest
  command, or before the newest read already accepted for that camera
  (request sequence numbers, not wall time). A stale in-flight read can
  therefore never overwrite a newer confirmed state. This is the on-off-on
  flip seen in the Shop history on 2026-10-07 03:11.
* A command leaves ``confirmed`` alone and records a ``Pending``. A matching
  accepted read resolves it (outcome ``confirmed``); the 60 s window expiring
  resolves it with outcome ``timeout``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .api import CameraInfo, CameraProps

ATTR_POWER = "power"
ATTR_SIREN = "siren"
ATTRIBUTES = (ATTR_POWER, ATTR_SIREN)


@dataclass
class Pending:
    """A command sent to Wyze and not yet confirmed by a read."""

    target: bool
    since: datetime
    seq: int

    @property
    def command(self) -> str:
        """``turn_on`` or ``turn_off``."""
        return "turn_on" if self.target else "turn_off"


@dataclass
class AttrState:
    """Confirmed value plus at most one pending command for power or siren."""

    confirmed: bool | None = None
    pending: Pending | None = None


@dataclass
class CommandResult:
    """Outcome of the latest command on a camera (diagnostic sensor)."""

    attribute: str
    command: str
    outcome: str
    sent_at: datetime
    finished_at: datetime
    reported: bool | None
    error: str | None = None

    @property
    def elapsed_s(self) -> float:
        """Seconds from send to resolution."""
        return round((self.finished_at - self.sent_at).total_seconds(), 1)

    def as_attributes(self) -> dict[str, Any]:
        """Entity attributes (no ids, tokens or bodies)."""
        return {
            "attribute": self.attribute,
            "command": self.command,
            "sent_at": self.sent_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "elapsed_s": self.elapsed_s,
            "reported_state": None if self.reported is None else ("on" if self.reported else "off"),
            "error": self.error,
        }


@dataclass
class CameraState:
    """Everything known about one camera."""

    info: CameraInfo
    power: AttrState = field(default_factory=AttrState)
    siren: AttrState = field(default_factory=AttrState)
    online: bool | None = None
    in_account: bool = True
    last_command_seq: int = 0
    last_accepted_read_seq: int = 0
    last_read_at: datetime | None = None
    last_read_error: str | None = None
    stale_reads_discarded: int = 0
    last_response_ms: int | None = None
    last_response_operation: str | None = None
    last_response_outcome: str | None = None
    last_response_at: datetime | None = None
    last_result: CommandResult | None = None

    def attr(self, name: str) -> AttrState:
        """Power or siren state."""
        return self.power if name == ATTR_POWER else self.siren

    @property
    def has_pending(self) -> bool:
        """True while any command awaits confirmation."""
        return self.power.pending is not None or self.siren.pending is not None

    def accept(self, seq: int, props: CameraProps, now: datetime) -> bool:
        """Apply a read that started with sequence ``seq``. False if discarded as stale."""
        if seq < self.last_command_seq or seq < self.last_accepted_read_seq:
            self.stale_reads_discarded += 1
            return False
        self.last_accepted_read_seq = seq
        self.last_read_at = now
        self.last_read_error = None
        if props.online is not None:
            self.online = props.online
        if props.power is not None:
            self.power.confirmed = props.power
        if props.siren is not None:
            self.siren.confirmed = props.siren
        return True

    def accept_offline(self, seq: int, now: datetime) -> bool:
        """Wyze answered 3019 (offline) to a read."""
        if seq < self.last_command_seq or seq < self.last_accepted_read_seq:
            self.stale_reads_discarded += 1
            return False
        self.last_accepted_read_seq = seq
        self.last_read_at = now
        self.last_read_error = None
        self.online = False
        return True

    def matched(self) -> list[str]:
        """Attributes whose pending target now equals the confirmed value."""
        return [
            name
            for name in ATTRIBUTES
            if (a := self.attr(name)).pending is not None and a.confirmed == a.pending.target
        ]

    def displayed(self, name: str, optimistic: bool) -> bool | None:
        """Value an entity shows: confirmed, or the pending target when optimistic."""
        a = self.attr(name)
        if optimistic and a.pending is not None:
            return a.pending.target
        return a.confirmed

    def pending_attributes(self, name: str, optimistic: bool) -> dict[str, Any]:
        """``pending_command`` / ``pending_since`` / ``optimistic`` attributes."""
        p = self.attr(name).pending
        return {
            "pending_command": p.command if p else None,
            "pending_since": p.since.isoformat() if p else None,
            "optimistic": optimistic,
        }
