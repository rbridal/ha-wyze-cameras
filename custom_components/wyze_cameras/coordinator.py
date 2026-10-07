"""Polling, commands and confirmation for every camera on one Wyze account."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import itertools
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ACTION_POWER_OFF,
    ACTION_POWER_ON,
    ACTION_SIREN_OFF,
    ACTION_SIREN_ON,
    RequestRecord,
    WyzeAuthError,
    WyzeClient,
    WyzeDeviceOffline,
    WyzeError,
    WyzeTwoFactorRequired,
)
from .const import (
    CONFIRM_SCHEDULE,
    CONFIRM_WINDOW,
    DEVICE_LIST_INTERVAL,
    DOMAIN,
    EVENT_COMMAND_RESULT,
    MAX_PARALLEL_READS,
    OPT_OPTIMISTIC_CAMERAS,
    OUTCOME_CONFIRMED,
    OUTCOME_FAILED,
    OUTCOME_OFFLINE,
    OUTCOME_SUPERSEDED,
    OUTCOME_TIMEOUT,
    POLL_FAILURES_BEFORE_UNAVAILABLE,
    POLL_INTERVAL,
)
from .latency import ApiStats
from .state import ATTR_POWER, ATTR_SIREN, CameraState, CommandResult, Pending

if TYPE_CHECKING:
    from . import WyzeCamerasConfigEntry

_LOGGER = logging.getLogger(__name__)

ACTIONS = {
    (ATTR_POWER, True): ACTION_POWER_ON,
    (ATTR_POWER, False): ACTION_POWER_OFF,
    (ATTR_SIREN, True): ACTION_SIREN_ON,
    (ATTR_SIREN, False): ACTION_SIREN_OFF,
}
UNIQUE_SUFFIX = {ATTR_POWER: ("switch", "power"), ATTR_SIREN: ("siren", "siren")}


def _onoff(value: bool | None) -> str | None:
    return None if value is None else ("on" if value else "off")


class WyzeCamerasCoordinator(DataUpdateCoordinator[dict[str, CameraState]]):
    """Owns the client, the camera states and the confirmation schedules."""

    config_entry: WyzeCamerasConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: WyzeCamerasConfigEntry, client: WyzeClient
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
            always_update=True,
        )
        self.client = client
        self.cameras: dict[str, CameraState] = {}
        self.stats = ApiStats()
        self._seq = itertools.count(1)
        self._locks: dict[str, asyncio.Lock] = {}
        self._confirm_unsub: dict[str, CALLBACK_TYPE] = {}
        self._timeout_unsub: dict[tuple[str, str], CALLBACK_TYPE] = {}
        self._confirm_step: dict[str, int] = {}
        self._last_device_refresh: datetime | None = None
        self._poll_failures = 0
        self.skipped_models: list[str] = []
        self.optimistic: set[str] = set(entry.options.get(OPT_OPTIMISTIC_CAMERAS, []))

    # -------------------------------------------------------------- helpers

    def next_seq(self) -> int:
        """Monotonic request sequence number."""
        return next(self._seq)

    @callback
    def record_request(self, rec: RequestRecord) -> None:
        """Client observer: account stats plus per-camera last response."""
        self.stats.add(rec, dt_util.now().date())
        if rec.camera and (state := self.cameras.get(rec.camera)) is not None:
            state.last_response_ms = round(max(0.0, rec.duration) * 1000)
            state.last_response_operation = rec.operation
            state.last_response_outcome = rec.outcome
            state.last_response_at = dt_util.utcnow()

    def is_optimistic(self, mac: str) -> bool:
        """Per-camera option (off by default)."""
        return mac in self.optimistic

    @callback
    def set_optimistic(self, macs: list[str]) -> None:
        """Apply a changed options flow without reloading."""
        self.optimistic = set(macs)
        self.async_update_listeners()

    def _lock(self, mac: str) -> asyncio.Lock:
        return self._locks.setdefault(mac, asyncio.Lock())

    # -------------------------------------------------------------- polling

    async def _refresh_devices(self) -> None:
        cameras = await self.client.get_cameras()
        self._last_device_refresh = dt_util.utcnow()
        seen: set[str] = set()
        skipped: list[str] = []
        initial = not self.cameras
        new = False
        for info in cameras:
            if not info.supported:
                skipped.append(info.model)
                continue
            seen.add(info.mac)
            if (state := self.cameras.get(info.mac)) is None:
                new = new or not initial
                self.cameras[info.mac] = CameraState(info=info)
            else:
                state.info = info
                state.in_account = True
        for mac, state in self.cameras.items():
            if mac not in seen and state.in_account:
                _LOGGER.warning("Camera %s is no longer on the Wyze account", state.info.nickname)
                state.in_account = False
        if skipped and skipped != self.skipped_models:
            _LOGGER.warning(
                "Skipping Wyze camera models that need the devicemgmt API (not supported): %s",
                ", ".join(sorted(set(skipped))),
            )
        self.skipped_models = skipped
        if new:
            _LOGGER.info("New Wyze camera found; reloading to add its entities")
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    async def read_camera(self, mac: str) -> bool:
        """One property read. True when it produced an accepted result."""
        state = self.cameras[mac]
        seq = self.next_seq()
        try:
            props = await self.client.get_properties(state.info)
        except WyzeDeviceOffline:
            accepted = state.accept_offline(seq, dt_util.utcnow())
        except WyzeAuthError, WyzeTwoFactorRequired:
            raise
        except WyzeError as err:
            state.last_read_error = str(err)
            _LOGGER.debug("Read failed for %s: %s", state.info.nickname, err)
            return False
        else:
            accepted = state.accept(seq, props, dt_util.utcnow())
        if not accepted:
            _LOGGER.debug("Discarded a stale read for %s", state.info.nickname)
            return True
        for name in state.matched():
            self._resolve(mac, name, OUTCOME_CONFIRMED)
        return True

    async def _async_update_data(self) -> dict[str, CameraState]:
        self.stats.roll(dt_util.now().date())
        try:
            if (
                self._last_device_refresh is None
                or dt_util.utcnow() - self._last_device_refresh >= DEVICE_LIST_INTERVAL
            ):
                await self._refresh_devices()
            sem = asyncio.Semaphore(MAX_PARALLEL_READS)

            async def _one(mac: str) -> bool:
                async with sem:
                    return await self.read_camera(mac)

            macs = [m for m, s in self.cameras.items() if s.in_account]
            results = await asyncio.gather(*(_one(m) for m in macs))
        except (WyzeAuthError, WyzeTwoFactorRequired) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WyzeError as err:
            return self._poll_failed(str(err))
        if macs and not any(results):
            return self._poll_failed("every camera read failed")
        self._poll_failures = 0
        return self.cameras

    def _poll_failed(self, reason: str) -> dict[str, CameraState]:
        self._poll_failures += 1
        if self._poll_failures >= POLL_FAILURES_BEFORE_UNAVAILABLE or self.data is None:
            raise UpdateFailed(f"Wyze poll failed: {reason}")
        _LOGGER.debug("Wyze poll failed once (%s); keeping last states", reason)
        return self.cameras

    # ------------------------------------------------------------- commands

    async def async_command(self, mac: str, name: str, target: bool) -> None:
        """Send power/siren on/off and start confirmation. Raises on failure."""
        state = self.cameras[mac]
        action = ACTIONS[(name, target)]
        async with self._lock(mac):
            seq = self.next_seq()
            # Bump before sending: any read already in flight is now stale.
            state.last_command_seq = seq
            sent_at = dt_util.utcnow()
            attr = state.attr(name)
            if attr.pending is not None:
                self._resolve(mac, name, OUTCOME_SUPERSEDED, notify=False)
            try:
                await self.client.run_action(state.info, action)
            except WyzeDeviceOffline as err:
                self._finish(mac, name, target, sent_at, OUTCOME_OFFLINE, str(err))
                self.async_update_listeners()
                raise HomeAssistantError(f"{state.info.nickname} is offline") from err
            except (WyzeAuthError, WyzeTwoFactorRequired) as err:
                self._finish(mac, name, target, sent_at, OUTCOME_FAILED, str(err))
                self.async_update_listeners()
                self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError(f"Wyze login needed: {err}") from err
            except WyzeError as err:
                self._finish(mac, name, target, sent_at, OUTCOME_FAILED, str(err))
                self.async_update_listeners()
                raise HomeAssistantError(
                    f"Wyze command failed for {state.info.nickname}: {err}"
                ) from err
            attr.pending = Pending(target=target, since=sent_at, seq=seq)
            self._schedule_timeout(mac, name, seq)
            self._restart_confirm(mac)
        self.async_update_listeners()

    def _schedule_timeout(self, mac: str, name: str, seq: int) -> None:
        key = (mac, name)
        if (unsub := self._timeout_unsub.pop(key, None)) is not None:
            unsub()

        @callback
        def _expire(_now: datetime) -> None:
            self._timeout_unsub.pop(key, None)
            pending = self.cameras[mac].attr(name).pending
            if pending is not None and pending.seq == seq:
                self._resolve(mac, name, OUTCOME_TIMEOUT)

        self._timeout_unsub[key] = async_call_later(
            self.hass, CONFIRM_WINDOW.total_seconds(), _expire
        )

    def _restart_confirm(self, mac: str) -> None:
        if (unsub := self._confirm_unsub.pop(mac, None)) is not None:
            unsub()
        self._confirm_step[mac] = 0
        self._schedule_confirm(mac)

    def _schedule_confirm(self, mac: str) -> None:
        step = self._confirm_step.get(mac, 0)
        if step >= len(CONFIRM_SCHEDULE):
            return

        @callback
        def _fire(_now: datetime) -> None:
            self._confirm_unsub.pop(mac, None)
            self.config_entry.async_create_background_task(
                self.hass, self._confirm_read(mac), f"{DOMAIN} confirm read"
            )

        self._confirm_unsub[mac] = async_call_later(self.hass, CONFIRM_SCHEDULE[step], _fire)

    async def _confirm_read(self, mac: str) -> None:
        state = self.cameras.get(mac)
        if state is None or not state.has_pending:
            return
        try:
            await self.read_camera(mac)
        except WyzeAuthError, WyzeTwoFactorRequired:
            self.config_entry.async_start_reauth(self.hass)
            return
        self.async_update_listeners()
        if state.has_pending:
            self._confirm_step[mac] = self._confirm_step.get(mac, 0) + 1
            self._schedule_confirm(mac)

    # ----------------------------------------------------------- resolution

    def _resolve(self, mac: str, name: str, outcome: str, notify: bool = True) -> None:
        state = self.cameras[mac]
        attr = state.attr(name)
        pending = attr.pending
        if pending is None:
            return
        attr.pending = None
        if (unsub := self._timeout_unsub.pop((mac, name), None)) is not None:
            unsub()
        if not state.has_pending and (unsub := self._confirm_unsub.pop(mac, None)) is not None:
            unsub()
        if outcome == OUTCOME_TIMEOUT:
            _LOGGER.warning(
                "%s %s: Wyze did not confirm %s within %d s; showing the reported state (%s)",
                state.info.nickname,
                name,
                pending.command,
                int(CONFIRM_WINDOW.total_seconds()),
                _onoff(attr.confirmed) or "unknown",
            )
        self._finish(mac, name, pending.target, pending.since, outcome, None)
        if notify:
            self.async_update_listeners()

    def _finish(
        self,
        mac: str,
        name: str,
        target: bool,
        sent_at: datetime,
        outcome: str,
        error: str | None,
    ) -> None:
        state = self.cameras[mac]
        result = CommandResult(
            attribute=name,
            command="turn_on" if target else "turn_off",
            outcome=outcome,
            sent_at=sent_at,
            finished_at=dt_util.utcnow(),
            reported=state.attr(name).confirmed,
            error=error,
        )
        state.last_result = result
        platform, suffix = UNIQUE_SUFFIX[name]
        entity_id = er.async_get(self.hass).async_get_entity_id(platform, DOMAIN, f"{mac}_{suffix}")
        self.hass.bus.async_fire(
            EVENT_COMMAND_RESULT,
            {
                "entity_id": entity_id,
                "camera": state.info.nickname,
                "attribute": name,
                "command": result.command,
                "outcome": outcome,
                "elapsed_s": result.elapsed_s,
                "reported_state": _onoff(result.reported),
                "optimistic": self.is_optimistic(mac),
            },
        )

    @callback
    def async_shutdown_timers(self) -> None:
        """Cancel every scheduled confirmation and timeout."""
        for unsub in [*self._confirm_unsub.values(), *self._timeout_unsub.values()]:
            unsub()
        self._confirm_unsub.clear()
        self._timeout_unsub.clear()

    def diagnostics(self, hash_id: Callable[[str], str]) -> dict[str, Any]:
        """Redaction-ready snapshot (MACs hashed by the caller's function)."""
        out: dict[str, Any] = {}
        for mac, s in self.cameras.items():
            out[hash_id(mac)] = {
                "model": s.info.model,
                "in_account": s.in_account,
                "online": s.online,
                "optimistic": self.is_optimistic(mac),
                "power": {
                    "confirmed": s.power.confirmed,
                    "pending": s.pending_attributes(ATTR_POWER, False),
                },
                "siren": {
                    "confirmed": s.siren.confirmed,
                    "pending": s.pending_attributes(ATTR_SIREN, False),
                },
                "last_read_at": s.last_read_at.isoformat() if s.last_read_at else None,
                "last_read_error": s.last_read_error,
                "stale_reads_discarded": s.stale_reads_discarded,
                "last_response_ms": s.last_response_ms,
                "last_command": (
                    {"outcome": s.last_result.outcome, **s.last_result.as_attributes()}
                    if s.last_result
                    else None
                ),
            }
        return out
