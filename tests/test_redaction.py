"""Diagnostics are redacted; logs never carry bodies, tokens, keys, URLs or MACs."""

from __future__ import annotations

import json
import logging

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wyze_cameras.api.client import hash_password
from custom_components.wyze_cameras.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    API_KEY,
    CHILLER,
    CONNECTION_ERROR,
    EMAIL,
    KEY_ID,
    OFFICE,
    OFFICE_POWER,
    PARKING,
    PASSWORD,
    PHONE_ID,
    FakeWyzeCloud,
    advance,
)

SECRETS = [EMAIL, PASSWORD, hash_password(PASSWORD), KEY_ID, API_KEY, PHONE_ID]


def _assert_clean(text: str, *, allow_nicknames: bool) -> None:
    for secret in SECRETS:
        assert secret not in text
    for mac in (OFFICE, CHILLER, PARKING):
        assert mac not in text
    assert "access-" not in text
    assert "refresh-" not in text
    assert "https://" not in text
    assert "wyzecam.com" not in text
    assert "api.wyze.com" not in text
    assert "192.168.1.50" not in text
    assert "ShopWiFi" not in text
    if not allow_nicknames:
        assert "Shop Office Cam" not in text


@pytest.mark.parametrize("optimistic", [[CHILLER]])
async def test_diagnostics_redacted(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
) -> None:
    """Credentials, tokens, MACs and nicknames never appear in diagnostics."""
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: OFFICE_POWER}, blocking=True
    )
    await advance(hass, freezer, 2)
    diag = await async_get_config_entry_diagnostics(hass, setup_entry)
    text = json.dumps(diag, default=str)
    _assert_clean(text, allow_nicknames=False)
    assert diag["entry"]["data"]["email"] == "**REDACTED**"
    assert diag["entry"]["data"]["access_token"] == "**REDACTED**"
    assert diag["entry"]["options"]["optimistic_cameras"][0].startswith("cam_")
    cams = diag["cameras"]
    assert len(cams) == 3
    assert all(k.startswith("cam_") for k in cams)
    office = next(c for c in cams.values() if c["model"] == "HL_CAM4")
    assert office["power"]["confirmed"] is True
    assert office["last_command"]["outcome"] == "confirmed"
    assert diag["api"]["requests_today"] > 0
    assert diag["poll"]["skipped_models"] == ["HL_PAN4"]


async def test_logs_never_leak(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: FakeWyzeCloud,
    setup_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DEBUG logs through commands, timeouts, token errors and failures stay clean."""
    caplog.set_level(logging.DEBUG, logger="custom_components.wyze_cameras")
    cloud.cameras[OFFICE].ignore_actions = True
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: OFFICE_POWER}, blocking=True
    )
    await advance(hass, freezer, 61, step=1)
    cloud.valid_access = {"rotated"}
    cloud.refresh_ok = False
    await advance(hass, freezer, 30, step=5)
    cloud.cameras[OFFICE].action_code = "1001"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_off", {ATTR_ENTITY_ID: OFFICE_POWER}, blocking=True
        )
    cloud.fail_all = CONNECTION_ERROR
    await advance(hass, freezer, 60, step=5)
    assert "did not confirm" in caplog.text
    _assert_clean(caplog.text, allow_nicknames=True)
