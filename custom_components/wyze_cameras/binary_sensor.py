"""Camera online diagnostic."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WyzeCamerasConfigEntry
from .entity import WyzeCameraEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WyzeCamerasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an online sensor per camera."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(WyzeOnline(coordinator, mac) for mac in coordinator.cameras)


class WyzeOnline(WyzeCameraEntity, BinarySensorEntity):
    """Wyze-reported connectivity (P5)."""

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, mac: str) -> None:  # type: ignore[no-untyped-def]
        """Initialize."""
        super().__init__(coordinator, mac, "online")

    @property
    def is_on(self) -> bool | None:
        """True when Wyze reports the camera online."""
        return self.cam.online

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """When Wyze last answered for this camera."""
        cam = self.cam
        return {
            "last_read_at": cam.last_read_at.isoformat() if cam.last_read_at else None,
            "last_read_error": cam.last_read_error,
            "stale_reads_discarded": cam.stale_reads_discarded,
        }
