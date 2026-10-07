"""Diagnostic sensors: response times, request counts, last command result."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WyzeCamerasConfigEntry
from .const import (
    OUTCOME_CONFIRMED,
    OUTCOME_FAILED,
    OUTCOME_OFFLINE,
    OUTCOME_SUPERSEDED,
    OUTCOME_TIMEOUT,
)
from .entity import WyzeAccountEntity, WyzeCameraEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WyzeCamerasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add camera and account diagnostics."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        AccountLastResponse(coordinator),
        AccountAverageResponse(coordinator),
        AccountRequestsToday(coordinator),
    ]
    for mac in coordinator.cameras:
        entities.append(CameraResponseTime(coordinator, mac))
        entities.append(CameraLastCommand(coordinator, mac))
    async_add_entities(entities)


class CameraResponseTime(WyzeCameraEntity, SensorEntity):
    """Duration of the latest Wyze request for this camera."""

    _attr_translation_key = "camera_response_time"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, mac: str) -> None:  # type: ignore[no-untyped-def]
        """Initialize."""
        super().__init__(coordinator, mac, "response_time")

    @property
    def available(self) -> bool:
        """Diagnostics stay available."""
        return True

    @property
    def native_value(self) -> int | None:
        """Milliseconds."""
        return self.cam.last_response_ms

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Operation and outcome of that request."""
        cam = self.cam
        return {
            "operation": cam.last_response_operation,
            "outcome": cam.last_response_outcome,
            "measured_at": cam.last_response_at.isoformat() if cam.last_response_at else None,
        }


class CameraLastCommand(WyzeCameraEntity, SensorEntity):
    """Outcome of the latest power/siren command on this camera."""

    _attr_translation_key = "last_command_result"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        OUTCOME_CONFIRMED,
        OUTCOME_TIMEOUT,
        OUTCOME_FAILED,
        OUTCOME_OFFLINE,
        OUTCOME_SUPERSEDED,
    ]

    def __init__(self, coordinator, mac: str) -> None:  # type: ignore[no-untyped-def]
        """Initialize."""
        super().__init__(coordinator, mac, "last_command_result")

    @property
    def available(self) -> bool:
        """Diagnostics stay available."""
        return True

    @property
    def native_value(self) -> str | None:
        """confirmed / timeout / failed / offline / superseded."""
        result = self.cam.last_result
        return result.outcome if result else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Command, timings and the state Wyze reported."""
        result = self.cam.last_result
        return result.as_attributes() if result else {}


class _AccountMs(WyzeAccountEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0


class AccountLastResponse(_AccountMs):
    """Latest request, whatever its outcome."""

    _attr_translation_key = "last_api_response_time"

    def __init__(self, coordinator) -> None:  # type: ignore[no-untyped-def]
        """Initialize."""
        super().__init__(coordinator, "last_api_response_time")

    @property
    def native_value(self) -> int | None:
        """Milliseconds."""
        return self.coordinator.stats.last_ms

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Operation and outcome."""
        return self.coordinator.stats.last_attributes()


class AccountAverageResponse(_AccountMs):
    """Mean over the last 20 requests that got a response."""

    _attr_translation_key = "average_api_response_time"

    def __init__(self, coordinator) -> None:  # type: ignore[no-untyped-def]
        """Initialize."""
        super().__init__(coordinator, "average_api_response_time")

    @property
    def native_value(self) -> int | None:
        """Milliseconds."""
        return self.coordinator.stats.average_ms

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Window size, sample count, failures, min, max."""
        return self.coordinator.stats.average_attributes()


class AccountRequestsToday(WyzeAccountEntity, SensorEntity):
    """HTTP requests to Wyze since local midnight (resets on restart)."""

    _attr_translation_key = "api_requests_today"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "requests"

    def __init__(self, coordinator) -> None:  # type: ignore[no-untyped-def]
        """Initialize."""
        super().__init__(coordinator, "api_requests_today")

    @property
    def native_value(self) -> int:
        """Count."""
        return self.coordinator.stats.requests_today

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """By operation and outcome."""
        return self.coordinator.stats.usage_attributes()
