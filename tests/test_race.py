"""The stale-poll race behind the slow Shop arm-away (2026-10-07 03:11).

What happened with ha-wyzeapi: the power switch went on (optimistic), a poll
that was already in flight when the command went out came back with P3=0 and
overwrote it (off), and the next poll ~30 s later finally showed on. The
``switch.shop_cams`` group (all: true) and the alarm template waited for that.

Here a read whose request started before the latest command is discarded, and
fresh confirmation reads run at 1/1/1/2/3/5/8/13/21 s, so the switch shows
on -> on only once Wyze reports P3=1, and never on -> off -> on.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.wyze_cameras.const import EVENT_COMMAND_RESULT

from .conftest import (
    OFFICE,
    OFFICE_POWER,
    FakeWyzeCloud,
    StateRecorder,
    advance,
    capture_events,
    spin,
)


async def _start_held_poll(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, cloud: FakeWyzeCloud, mac: str
) -> asyncio.Event:
    """Let the 30 s poll start and park its read for ``mac`` in flight."""
    gate = asyncio.Event()
    cloud.hold_reads[mac] = gate
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await spin(lambda: cloud.held == 1)
    return gate


async def _race(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
) -> tuple[StateRecorder, list[dict]]:
    """Reproduce the 03:11 sequence: poll in flight, power_on, stale P3=0 lands."""
    cam = cloud.cameras[OFFICE]
    cam.power_delay = 4.0  # camera boots ~4 s before Wyze reports P3=1
    rec = StateRecorder(hass, OFFICE_POWER)
    events = capture_events(hass, EVENT_COMMAND_RESULT)

    gate = await _start_held_poll(hass, freezer, cloud, OFFICE)
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: OFFICE_POWER}, blocking=True
    )
    assert cloud.actions(OFFICE) == ["power_on"]

    # The in-flight read (started before the command, snapshot P3=0) lands now.
    gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    return rec, events


async def test_stale_poll_never_flips_confirmed_switch(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """Default (confirmed-only): off until Wyze reports on, then on. No flip."""
    coordinator = setup_entry.runtime_data.coordinator
    rec, events = await _race(hass, freezer, cloud)

    state = hass.states.get(OFFICE_POWER)
    assert state.state == STATE_OFF
    assert state.attributes["pending_command"] == "turn_on"
    assert state.attributes["pending_since"] is not None
    assert state.attributes["optimistic"] is False
    assert coordinator.cameras[OFFICE].stale_reads_discarded == 1

    # Confirmation reads at +1, +2, +3 still see P3=0 (booting); +5 sees P3=1.
    await advance(hass, freezer, 3)
    assert hass.states.get(OFFICE_POWER).state == STATE_OFF
    await advance(hass, freezer, 2)
    state = hass.states.get(OFFICE_POWER)
    assert state.state == STATE_ON
    assert state.attributes["pending_command"] is None

    assert rec.states == [STATE_OFF, STATE_ON]
    assert [e["outcome"] for e in events] == ["confirmed"]
    assert events[0]["entity_id"] == OFFICE_POWER
    assert events[0]["elapsed_s"] <= 5.0

    # And it stays on through later polls.
    await advance(hass, freezer, 90, step=5)
    assert rec.states == [STATE_OFF, STATE_ON]


@pytest.mark.parametrize("optimistic", [[OFFICE]])
async def test_stale_poll_never_flips_optimistic_switch(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """Optimistic camera: on at once, and the stale P3=0 cannot turn it off."""
    rec, events = await _race(hass, freezer, cloud)

    state = hass.states.get(OFFICE_POWER)
    assert state.state == STATE_ON
    assert state.attributes["pending_command"] == "turn_on"
    assert state.attributes["optimistic"] is True

    # Fresh reads before the camera reports P3=1 do not turn it off either.
    await advance(hass, freezer, 3)
    assert hass.states.get(OFFICE_POWER).state == STATE_ON
    assert hass.states.get(OFFICE_POWER).attributes["pending_command"] == "turn_on"
    await advance(hass, freezer, 2)
    assert hass.states.get(OFFICE_POWER).attributes["pending_command"] is None

    assert rec.states == [STATE_OFF, STATE_ON]
    assert [e["outcome"] for e in events] == ["confirmed"]
    assert events[0]["optimistic"] is True


async def test_out_of_order_reads_keep_newest(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """An older read finishing after a newer one is discarded too."""
    coordinator = setup_entry.runtime_data.coordinator
    cam = cloud.cameras[OFFICE]

    gate = await _start_held_poll(hass, freezer, cloud, OFFICE)  # snapshot P3=0
    cam.power = True  # changed in the Wyze app
    assert await coordinator.read_camera(OFFICE)  # newer read: P3=1
    coordinator.async_update_listeners()
    assert hass.states.get(OFFICE_POWER).state == STATE_ON

    gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(OFFICE_POWER).state == STATE_ON
    assert coordinator.cameras[OFFICE].stale_reads_discarded == 1


@pytest.mark.parametrize("optimistic", [[], [OFFICE]])
async def test_stale_read_landing_after_confirmation(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
    optimistic: list[str],
) -> None:
    """The exact 03:11 shape: on is shown, then a pre-command P3=0 read lands late.

    ha-wyzeapi applied it (on -> off) and waited ~30 s for the next poll (-> on).
    Here it is discarded: the state goes off -> on once and stays on.
    """
    coordinator = setup_entry.runtime_data.coordinator
    rec = StateRecorder(hass, OFFICE_POWER)
    gate = await _start_held_poll(hass, freezer, cloud, OFFICE)  # snapshot P3=0
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: OFFICE_POWER}, blocking=True
    )
    # The +1 s confirmation read sees P3=1 while the old poll is still in flight.
    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await spin(lambda: hass.states.get(OFFICE_POWER).attributes["pending_command"] is None)
    assert hass.states.get(OFFICE_POWER).state == STATE_ON

    gate.set()  # the stale P3=0 finally arrives
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(OFFICE_POWER).state == STATE_ON
    assert coordinator.cameras[OFFICE].stale_reads_discarded == 1
    await advance(hass, freezer, 60, step=5)
    assert rec.states == [STATE_OFF, STATE_ON]
