"""Token handling, setup failures, headers."""

from __future__ import annotations

import time

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wyze_cameras.const import (
    DATA_ACCESS_TOKEN,
    DATA_REFRESH_TOKEN,
    DOMAIN,
    USER_AGENT,
)

from .conftest import (
    API_KEY,
    CONNECTION_ERROR,
    EMAIL,
    KEY_ID,
    OFFICE,
    OFFICE_POWER,
    PHONE_ID,
    FakeWyzeCloud,
    advance,
    entry_data,
    make_entry,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_user_agent_and_auth_headers(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """Every request carries the repo User-Agent; login carries keyid/apikey."""
    await _setup(hass, make_entry(access_token=None, refresh_token=None))
    assert cloud.calls[0] == ("login", None)
    assert all(h.get("User-Agent") == USER_AGENT for h in cloud.headers)
    assert "github.com/rbridal/ha-wyze-cameras" in USER_AGENT
    assert cloud.headers[0]["keyid"] == KEY_ID
    assert cloud.headers[0]["apikey"] == API_KEY


async def test_token_error_refreshes_and_keeps_entry_data(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """2001 -> refresh -> retry; refreshed tokens saved with every other key kept."""
    before = dict(setup_entry.data)
    cloud.valid_access = {"server-rotated"}  # the stored access token is now rejected
    await advance(hass, freezer, 35, step=5)
    assert ("refresh_token", None) in cloud.calls
    assert hass.states.get(OFFICE_POWER).state == STATE_OFF
    data = setup_entry.data
    assert data[DATA_ACCESS_TOKEN] in cloud.valid_access
    assert data[DATA_REFRESH_TOKEN] in cloud.valid_refresh
    for key in ("email", "password_hash", "key_id", "api_key", "phone_id"):
        assert data[key] == before[key]
    assert setup_entry.state is ConfigEntryState.LOADED


async def test_refresh_rejected_falls_back_to_login(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """Refresh token rejected -> full login with the stored hash."""
    cloud.valid_access = {"server-rotated"}
    cloud.refresh_ok = False
    await advance(hass, freezer, 35, step=5)
    ops = [op for op, _ in cloud.calls]
    assert "refresh_token" in ops
    assert "login" in ops
    assert hass.states.get(OFFICE_POWER).state == STATE_OFF


async def test_proactive_refresh(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """Past token_refresh_at the client refreshes before calling."""
    await _setup(hass, make_entry(token_refresh_at=time.time() - 1))
    assert cloud.calls[0] == ("refresh_token", None)


async def test_login_rejected_starts_reauth(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """No tokens and a rejected login -> setup error + reauth flow."""
    entry = make_entry(access_token=None, refresh_token=None, api_key="revoked")
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_login_needs_2fa_starts_reauth(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """A background login that hits 2FA cannot finish alone -> reauth."""
    cloud.mfa = "totp"
    entry = make_entry(access_token=None, refresh_token=None)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.config_entries.flow.async_progress()[0]["context"]["source"] == "reauth"


async def test_setup_retry_on_network_error(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """Network down at startup -> retry later, no reauth."""
    cloud.fail_all = CONNECTION_ERROR
    entry = make_entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


async def test_missing_hash_reauth(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """An entry without the password hash asks for reauth."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data=entry_data(password_hash=None))
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_poll_failure_tolerance(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """One failed poll keeps states; two in a row -> unavailable; recovers."""
    cloud.fail_all = CONNECTION_ERROR
    await advance(hass, freezer, 30, step=5)
    assert hass.states.get(OFFICE_POWER).state == STATE_OFF
    await advance(hass, freezer, 30, step=5)
    assert hass.states.get(OFFICE_POWER).state == STATE_UNAVAILABLE
    cloud.fail_all = None
    await advance(hass, freezer, 30, step=5)
    assert hass.states.get(OFFICE_POWER).state == STATE_OFF


async def test_camera_removed_from_account(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """A camera gone from the device list goes unavailable at the next list refresh."""
    del cloud.cameras[OFFICE]
    await advance(hass, freezer, 46 * 60, step=30)
    assert hass.states.get(OFFICE_POWER).state == STATE_UNAVAILABLE


async def test_phone_id_is_stable(
    hass: HomeAssistant, cloud: FakeWyzeCloud, setup_entry: MockConfigEntry
) -> None:
    """The stored phone id is sent on app calls."""
    assert setup_entry.data["phone_id"] == PHONE_ID


async def test_new_camera_triggers_reload(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """A camera added to the account appears after the next device-list refresh."""
    from .conftest import FakeCamera

    cloud.cameras["AAAAAA000009"] = FakeCamera("AAAAAA000009", "Shop Garage Cam", "HL_CAM4")
    await advance(hass, freezer, 46 * 60, step=60)
    await hass.async_block_till_done()
    assert hass.states.get("switch.shop_garage_cam_power") is not None


async def test_usage_counters(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """Account sensors count requests and report latency."""
    first = int(hass.states.get("sensor.wyze_account_api_requests_today").state)
    await advance(hass, freezer, 35, step=5)
    state = hass.states.get("sensor.wyze_account_api_requests_today")
    assert int(state.state) == first + 3
    assert state.attributes["by_operation"]["get_property_list"] >= 6
    assert hass.states.get("sensor.wyze_account_last_api_response_time").state != "unknown"
    assert (
        hass.states.get("sensor.shop_office_cam_api_response_time").attributes["operation"]
        == "get_property_list"
    )
