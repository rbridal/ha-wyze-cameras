"""Camera siren."""

from __future__ import annotations

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WyzeCamerasConfigEntry
from .control import ConfirmedOnOffMixin
from .state import ATTR_SIREN

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WyzeCamerasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one siren per camera model that has one."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        WyzeSiren(coordinator, mac)
        for mac, cam in coordinator.cameras.items()
        if cam.info.has_siren
    )


class WyzeSiren(ConfirmedOnOffMixin, SirenEntity):
    """Camera siren (Wyze run_action siren_on / siren_off)."""

    attribute = ATTR_SIREN
    _attr_translation_key = "siren"
    _attr_supported_features = SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF

    def __init__(self, coordinator, mac: str) -> None:  # type: ignore[no-untyped-def]
        """Initialize."""
        super().__init__(coordinator, mac, "siren")
