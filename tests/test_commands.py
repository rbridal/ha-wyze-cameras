"""Commands: confirmed-only state, 60 s timeout, optimistic option, offline."""

from __future__ import annotations

import logging

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wyze_cameras.const import EVENT_COMMAND_RESULT, OPT_OPTIMISTIC_CAMERAS

from .conftest import (
    CHILLER,
    CHILLER_POWER,
    OFFICE,
    OFFICE_POWER,
    OFFICE_SIREN,
    PARKING,
    PARKING_POWER,
    FakeWyzeCloud,
    StateRecorder,
    advance,
    capture_events,
)

LAST_RESULT = "sensor.shop_office_cam_last_command_result"


async def _turn(hass: HomeAssistant, entity_id: str, on: bool) -> None:
    domain = entity_id.split(".", maxsplit=1)[0]
    await hass.services.async_call(
        domain, "turn_on" if on else "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_entities_and_unique_ids(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Entity ids match what ha-wyzeapi produced; unique ids are MAC based."""
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    assert reg.async_get(OFFICE_POWER).unique_id == f"{OFFICE}_power"
    assert reg.async_get(OFFICE_SIREN).unique_id == f"{OFFICE}_siren"
    assert hass.states.get(OFFICE_POWER).state == STATE_OFF
    assert hass.states.get(OFFICE_SIREN).state == STATE_OFF
    assert hass.states.get(PARKING_POWER).state == STATE_OFF
    assert hass.states.get("binary_sensor.shop_office_cam_online").state == STATE_ON
    assert hass.states.get("sensor.shop_office_cam_api_response_time") is not None
    assert hass.states.get(LAST_RESULT).state == "unknown"
    assert hass.states.get("sensor.wyze_account_api_requests_today").state not in (
        None,
        "unknown",
    )
    # Unsupported (devicemgmt) camera and non-camera devices are skipped.
    ids = {e.unique_id for e in reg.entities.values() if e.platform == "wyze_cameras"}
    assert not any(uid.startswith(("AAAAAA000004", "AAAAAA000005")) for uid in ids)


async def test_confirmed_after_first_matching_read(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """Non-optimistic: prior state + pending attrs, then flips at the confirming read."""
    events = capture_events(hass, EVENT_COMMAND_RESULT)
    await _turn(hass, OFFICE_POWER, True)
    state = hass.states.get(OFFICE_POWER)
    assert state.state == STATE_OFF
    assert state.attributes["pending_command"] == "turn_on"
    reads = cloud.reads(OFFICE)

    await advance(hass, freezer, 1)
    assert cloud.reads(OFFICE) == reads + 1
    state = hass.states.get(OFFICE_POWER)
    assert state.state == STATE_ON
    assert state.attributes["pending_command"] is None
    assert state.attributes["pending_since"] is None
    assert events == [
        {
            "entity_id": OFFICE_POWER,
            "camera": "Shop Office Cam",
            "attribute": "power",
            "command": "turn_on",
            "outcome": "confirmed",
            "elapsed_s": 1.0,
            "reported_state": "on",
            "optimistic": False,
        }
    ]
    result = hass.states.get(LAST_RESULT)
    assert result.state == "confirmed"
    assert result.attributes["command"] == "turn_on"
    assert result.attributes["elapsed_s"] == 1.0

    # No more confirmation reads once confirmed (only the regular poll).
    await advance(hass, freezer, 25)
    assert cloud.reads(OFFICE) == reads + 1


async def test_confirmation_schedule(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """Reads at +1, +2, +3, +5, +8, +13 ... while unconfirmed."""
    cloud.cameras[OFFICE].power_delay = 12.5
    await _turn(hass, OFFICE_POWER, True)
    base = cloud.reads(OFFICE)
    seen = []
    for _ in range(1, 14):
        await advance(hass, freezer, 1)
        seen.append(cloud.reads(OFFICE) - base)
    # cumulative counts at t=1..13
    assert seen == [1, 2, 3, 3, 4, 4, 4, 5, 5, 5, 5, 5, 6]
    assert hass.states.get(OFFICE_POWER).state == STATE_ON


async def test_timeout_keeps_reported_state(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """60 s without confirmation: real state stays, WARNING, timeout event/result."""
    cloud.cameras[OFFICE].ignore_actions = True
    events = capture_events(hass, EVENT_COMMAND_RESULT)
    rec = StateRecorder(hass, OFFICE_POWER)
    await _turn(hass, OFFICE_POWER, True)

    await advance(hass, freezer, 59)
    assert hass.states.get(OFFICE_POWER).attributes["pending_command"] == "turn_on"
    assert events == []
    with caplog.at_level(logging.WARNING):
        await advance(hass, freezer, 1)
    state = hass.states.get(OFFICE_POWER)
    assert state.state == STATE_OFF
    assert state.attributes["pending_command"] is None
    assert rec.states == [STATE_OFF]
    assert [e["outcome"] for e in events] == ["timeout"]
    assert events[0]["reported_state"] == "off"
    assert hass.states.get(LAST_RESULT).state == "timeout"
    assert "Wyze did not confirm turn_on within 60 s" in caplog.text
    assert "showing the reported state (off)" in caplog.text


@pytest.mark.parametrize("optimistic", [[CHILLER]])
async def test_optimistic_timeout_reverts(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Optimistic camera shows the command at once and reverts at 60 s if unconfirmed."""
    cloud.cameras[CHILLER].ignore_actions = True
    events = capture_events(hass, EVENT_COMMAND_RESULT)
    rec = StateRecorder(hass, CHILLER_POWER)
    await _turn(hass, CHILLER_POWER, True)
    state = hass.states.get(CHILLER_POWER)
    assert state.state == STATE_ON
    assert state.attributes["optimistic"] is True
    assert state.attributes["pending_command"] == "turn_on"
    # Other cameras stay confirmed-only.
    assert hass.states.get(OFFICE_POWER).attributes["optimistic"] is False

    await advance(hass, freezer, 59)
    assert hass.states.get(CHILLER_POWER).state == STATE_ON
    await advance(hass, freezer, 1)
    assert hass.states.get(CHILLER_POWER).state == STATE_OFF
    assert rec.states == [STATE_OFF, STATE_ON, STATE_OFF]
    assert events[0]["outcome"] == "timeout"
    assert events[0]["optimistic"] is True
    assert "Shop Chiller Cam power: Wyze did not confirm turn_on" in caplog.text


@pytest.mark.parametrize("optimistic", [[CHILLER]])
async def test_optimistic_confirmed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """Optimistic camera: on immediately, stays on when Wyze confirms."""
    cloud.cameras[CHILLER].power_delay = 3
    rec = StateRecorder(hass, CHILLER_POWER)
    await _turn(hass, CHILLER_POWER, True)
    await advance(hass, freezer, 5)
    assert hass.states.get(CHILLER_POWER).attributes["pending_command"] is None
    assert rec.states == [STATE_OFF, STATE_ON]


async def test_options_flow_toggles_optimistic_without_reload(
    hass: HomeAssistant, cloud: FakeWyzeCloud, setup_entry: MockConfigEntry
) -> None:
    """The options flow lists cameras by MAC and applies immediately."""
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["step_id"] == "init"
    schema = result["data_schema"].schema
    key = next(k for k in schema if k == OPT_OPTIMISTIC_CAMERAS)
    options = {o["value"]: o["label"] for o in schema[key].config["options"]}
    assert options == {
        CHILLER: "Shop Chiller Cam (HL_PAN2)",
        OFFICE: "Shop Office Cam (HL_CAM4)",
        PARKING: "Shop Parking Cam (WYZE_CAKP2JFUS)",
    }
    coordinator = setup_entry.runtime_data.coordinator
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_OPTIMISTIC_CAMERAS: [CHILLER]}
    )
    await hass.async_block_till_done()
    assert setup_entry.options == {OPT_OPTIMISTIC_CAMERAS: [CHILLER]}
    assert setup_entry.runtime_data.coordinator is coordinator  # not reloaded
    assert hass.states.get(CHILLER_POWER).attributes["optimistic"] is True
    assert hass.states.get(OFFICE_POWER).attributes["optimistic"] is False


async def test_siren_confirmed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """Siren uses the same confirmed-only flow."""
    cloud.cameras[OFFICE].siren_delay = 1.5
    await _turn(hass, OFFICE_SIREN, True)
    assert cloud.actions(OFFICE) == ["siren_on"]
    assert hass.states.get(OFFICE_SIREN).state == STATE_OFF
    assert hass.states.get(OFFICE_SIREN).attributes["pending_command"] == "turn_on"
    await advance(hass, freezer, 2)
    assert hass.states.get(OFFICE_SIREN).state == STATE_ON
    await _turn(hass, OFFICE_SIREN, False)
    assert hass.states.get(OFFICE_SIREN).state == STATE_ON
    await advance(hass, freezer, 2)
    assert hass.states.get(OFFICE_SIREN).state == STATE_OFF
    assert cloud.actions(OFFICE) == ["siren_on", "siren_off"]


async def test_superseded_command(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """A second command replaces the pending one (outcome superseded)."""
    cloud.cameras[OFFICE].ignore_actions = True
    events = capture_events(hass, EVENT_COMMAND_RESULT)
    await _turn(hass, OFFICE_POWER, True)
    cloud.cameras[OFFICE].ignore_actions = False
    await _turn(hass, OFFICE_POWER, False)
    assert hass.states.get(OFFICE_POWER).attributes["pending_command"] == "turn_off"
    await advance(hass, freezer, 1)
    assert [e["outcome"] for e in events] == ["superseded", "confirmed"]
    assert hass.states.get(OFFICE_POWER).state == STATE_OFF
    await advance(hass, freezer, 70, step=5)
    assert [e["outcome"] for e in events] == ["superseded", "confirmed"]


async def test_offline_camera_read(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """3019 on read: that camera's controls unavailable, others unaffected."""
    cloud.cameras[CHILLER].read_code = "3019"
    await advance(hass, freezer, 35, step=5)
    assert hass.states.get(CHILLER_POWER).state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.shop_chiller_cam_online").state == STATE_OFF
    assert hass.states.get(OFFICE_POWER).state == STATE_OFF
    # Comes back when Wyze reports it again.
    cloud.cameras[CHILLER].read_code = "1"
    await advance(hass, freezer, 35, step=5)
    assert hass.states.get(CHILLER_POWER).state == STATE_OFF


async def test_reported_offline_flag(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """P5=0 also marks the controls unavailable."""
    cloud.cameras[PARKING].online = False
    await advance(hass, freezer, 35, step=5)
    assert hass.states.get(PARKING_POWER).state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.shop_parking_cam_online").state == STATE_OFF


async def test_offline_command(
    hass: HomeAssistant, cloud: FakeWyzeCloud, setup_entry: MockConfigEntry
) -> None:
    """3019 on run_action: error raised, outcome offline, no pending state."""
    cloud.cameras[OFFICE].action_code = "3019"
    events = capture_events(hass, EVENT_COMMAND_RESULT)
    with pytest.raises(HomeAssistantError, match="offline"):
        await _turn(hass, OFFICE_POWER, True)
    await hass.async_block_till_done()
    assert hass.states.get(OFFICE_POWER).attributes["pending_command"] is None
    assert [e["outcome"] for e in events] == ["offline"]
    assert hass.states.get(LAST_RESULT).state == "offline"


async def test_failed_command(
    hass: HomeAssistant, cloud: FakeWyzeCloud, setup_entry: MockConfigEntry
) -> None:
    """Other API codes: error raised, outcome failed."""
    cloud.cameras[OFFICE].action_code = "1001"
    events = capture_events(hass, EVENT_COMMAND_RESULT)
    with pytest.raises(HomeAssistantError, match="code 1001"):
        await _turn(hass, OFFICE_POWER, True)
    await hass.async_block_till_done()
    assert [e["outcome"] for e in events] == ["failed"]
    assert hass.states.get(LAST_RESULT).state == "failed"
    assert hass.states.get(OFFICE_POWER).attributes["pending_command"] is None
