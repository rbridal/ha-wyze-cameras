"""Base entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WyzeCamerasCoordinator
from .state import CameraState


class WyzeCameraEntity(CoordinatorEntity[WyzeCamerasCoordinator]):
    """Entity on one camera device.

    The device is named after the Wyze nickname and entity names are short
    ("Power", "Siren"), so a camera called "Shop Office Cam" gets
    ``switch.shop_office_cam_power`` and ``siren.shop_office_cam_siren``:
    the same entity ids ha-wyzeapi produced.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: WyzeCamerasCoordinator, mac: str, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.mac = mac
        self._attr_unique_id = f"{mac}_{key}"
        info = coordinator.cameras[mac].info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            name=info.nickname,
            manufacturer=MANUFACTURER,
            model=info.model,
            sw_version=info.firmware,
        )

    @property
    def cam(self) -> CameraState:
        """This camera's state."""
        return self.coordinator.cameras[self.mac]

    @property
    def available(self) -> bool:
        """Unavailable when the account polls keep failing or the camera left the account."""
        return super().available and self.cam.in_account


class WyzeAccountEntity(CoordinatorEntity[WyzeCamerasCoordinator]):
    """Entity on the account service device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WyzeCamerasCoordinator, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account_{entry_id}")},
            name="Wyze account",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Account diagnostics stay available while the entry is loaded."""
        return True
