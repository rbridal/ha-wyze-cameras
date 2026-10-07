"""Shared on/off behaviour for the power switch and the siren."""

from __future__ import annotations

from typing import Any

from .entity import WyzeCameraEntity


class ConfirmedOnOffMixin(WyzeCameraEntity):
    """Shows confirmed Wyze state; optimistic per camera only when opted in.

    Unavailable while Wyze reports the camera offline (P5 = 0), as ha-wyzeapi
    did, so alarm templates see "unavailable" rather than a stale "on".
    """

    attribute: str

    @property
    def available(self) -> bool:
        """Also unavailable when Wyze says the camera is offline."""
        return super().available and self.cam.online is not False

    @property
    def is_on(self) -> bool | None:
        """Confirmed value, or the pending target for optimistic cameras."""
        return self.cam.displayed(self.attribute, self.coordinator.is_optimistic(self.mac))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """pending_command / pending_since / optimistic."""
        return self.cam.pending_attributes(self.attribute, self.coordinator.is_optimistic(self.mac))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send the on command; state follows confirmation."""
        await self.coordinator.async_command(self.mac, self.attribute, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send the off command; state follows confirmation."""
        await self.coordinator.async_command(self.mac, self.attribute, False)
