"""Camera power switch."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WyzeCamerasConfigEntry
from .control import ConfirmedOnOffMixin
from .state import ATTR_POWER

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WyzeCamerasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one power switch per camera that can power off."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        WyzePowerSwitch(coordinator, mac)
        for mac, cam in coordinator.cameras.items()
        if cam.info.has_power
    )


class WyzePowerSwitch(ConfirmedOnOffMixin, SwitchEntity):
    """Camera power (Wyze run_action power_on / power_off)."""

    attribute = ATTR_POWER
    _attr_translation_key = "power"

    def __init__(self, coordinator, mac: str) -> None:  # type: ignore[no-untyped-def]
        """Initialize."""
        super().__init__(coordinator, mac, "power")
