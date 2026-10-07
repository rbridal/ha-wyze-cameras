"""Diagnostics with redaction.

Email, password hash, API key ID/key, tokens, phone id and nicknames are
redacted. MACs are replaced by a stable short hash (``cam_3f9a``) so a user
can point at "this camera" without publishing an identifier. No URLs or
response bodies are kept anywhere, so none can leak here.
"""

from __future__ import annotations

import hashlib
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from . import WyzeCamerasConfigEntry
from .const import (
    CONF_API_KEY,
    CONF_KEY_ID,
    DATA_ACCESS_TOKEN,
    DATA_PASSWORD_HASH,
    DATA_PHONE_ID,
    DATA_REFRESH_TOKEN,
    OPT_OPTIMISTIC_CAMERAS,
    VERSION,
)

TO_REDACT = {
    CONF_EMAIL,
    DATA_PASSWORD_HASH,
    CONF_KEY_ID,
    CONF_API_KEY,
    DATA_ACCESS_TOKEN,
    DATA_REFRESH_TOKEN,
    DATA_PHONE_ID,
    "password",
    "nickname",
    "mac",
}


def hash_id(mac: str) -> str:
    """Stable short hash for a camera MAC."""
    return "cam_" + hashlib.sha256(mac.encode()).hexdigest()[:4]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WyzeCamerasConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics."""
    coordinator = entry.runtime_data.coordinator
    options = dict(entry.options)
    options[OPT_OPTIMISTIC_CAMERAS] = [hash_id(m) for m in options.get(OPT_OPTIMISTIC_CAMERAS, [])]
    return {
        "integration_version": VERSION,
        "home_assistant": HA_VERSION,
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": options,
        },
        "poll": {
            "last_update_success": coordinator.last_update_success,
            "skipped_models": coordinator.skipped_models,
        },
        "cameras": coordinator.diagnostics(hash_id),
        "api": coordinator.stats.snapshot(),
    }
