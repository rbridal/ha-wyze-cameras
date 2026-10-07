"""Wyze Cameras: power and siren control for Wyze cameras, nothing else."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Tokens, WyzeClient
from .const import (
    CONF_API_KEY,
    CONF_KEY_ID,
    DATA_ACCESS_TOKEN,
    DATA_PASSWORD_HASH,
    DATA_PHONE_ID,
    DATA_REFRESH_TOKEN,
    DATA_TOKEN_REFRESH_AT,
    OPT_OPTIMISTIC_CAMERAS,
    REQUEST_TIMEOUT,
    TOKEN_REFRESH_AFTER,
    USER_AGENT,
)
from .coordinator import WyzeCamerasCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SIREN, Platform.SWITCH]


@dataclass
class WyzeCamerasRuntime:
    """Runtime data for one entry."""

    coordinator: WyzeCamerasCoordinator


type WyzeCamerasConfigEntry = ConfigEntry[WyzeCamerasRuntime]


def build_client(hass: HomeAssistant, entry: WyzeCamerasConfigEntry) -> WyzeClient:
    """Client from stored entry data, saving refreshed tokens back (all keys kept)."""
    data = entry.data
    tokens = None
    if data.get(DATA_ACCESS_TOKEN) and data.get(DATA_REFRESH_TOKEN):
        tokens = Tokens(
            data[DATA_ACCESS_TOKEN],
            data[DATA_REFRESH_TOKEN],
            float(data.get(DATA_TOKEN_REFRESH_AT) or 0),
        )

    def _save(new: Tokens) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                DATA_ACCESS_TOKEN: new.access_token,
                DATA_REFRESH_TOKEN: new.refresh_token,
                DATA_TOKEN_REFRESH_AT: new.refresh_at,
            },
        )

    return WyzeClient(
        async_get_clientsession(hass),
        email=data[CONF_EMAIL],
        password_hash=data[DATA_PASSWORD_HASH],
        key_id=data[CONF_KEY_ID],
        api_key=data[CONF_API_KEY],
        phone_id=data[DATA_PHONE_ID],
        user_agent=USER_AGENT,
        tokens=tokens,
        refresh_after=TOKEN_REFRESH_AFTER.total_seconds(),
        request_timeout=REQUEST_TIMEOUT,
        on_tokens=_save,
    )


async def async_setup_entry(hass: HomeAssistant, entry: WyzeCamerasConfigEntry) -> bool:
    """Set up from a config entry."""
    if not entry.data.get(DATA_PASSWORD_HASH):
        raise ConfigEntryAuthFailed("Wyze credentials missing")
    client = build_client(hass, entry)
    coordinator = WyzeCamerasCoordinator(hass, entry, client)
    client.set_observer(coordinator.record_request)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = WyzeCamerasRuntime(coordinator=coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    entry.async_on_unload(coordinator.async_shutdown_timers)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: WyzeCamerasConfigEntry) -> None:
    """Apply the per-camera optimistic option without a reload."""
    entry.runtime_data.coordinator.set_optimistic(entry.options.get(OPT_OPTIMISTIC_CAMERAS, []))


async def async_unload_entry(hass: HomeAssistant, entry: WyzeCamerasConfigEntry) -> bool:
    """Unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
