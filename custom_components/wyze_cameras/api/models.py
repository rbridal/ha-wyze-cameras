"""Data models for the parts of the Wyze API this integration uses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PRODUCT_TYPE_CAMERA = "Camera"

# get_property_list ids (wyzeapy PropertyIDs)
PID_POWER = "P3"
PID_ONLINE = "P5"
PID_SIREN = "P1049"

# Models without a siren (from upstream ha-wyzeapi siren.py). None are owned.
NO_SIREN_MODELS = frozenset({"WYZECP1_JEF", "WYZEC1-JZ", "GW_BE1"})
# Models with no power-off function (video doorbell pro).
NO_POWER_MODELS = frozenset({"GW_BE1"})
# Models that need Wyze's newer devicemgmt API. Not implemented here (none are
# owned); such cameras are skipped with a warning.
DEVICEMGMT_MODELS = frozenset({"LD_CFP", "AN_RSCW", "GW_GC1", "HL_PAN4"})


def _flag(value: Any) -> bool | None:
    """Wyze sends property values as "1"/"0" strings (sometimes ints)."""
    if value is None or value == "":
        return None
    return str(value).strip() == "1"


@dataclass(frozen=True)
class CameraInfo:
    """One camera from get_object_list."""

    mac: str
    nickname: str
    model: str
    firmware: str | None = None

    @property
    def has_siren(self) -> bool:
        """True when the model has a siren."""
        return self.model not in NO_SIREN_MODELS

    @property
    def has_power(self) -> bool:
        """True when the model can be powered off and on."""
        return self.model not in NO_POWER_MODELS

    @property
    def supported(self) -> bool:
        """True when the classic property/run_action API applies."""
        return self.model not in DEVICEMGMT_MODELS

    @classmethod
    def from_device(cls, raw: dict[str, Any]) -> CameraInfo | None:
        """Build from one get_object_list device record, or None if not a camera."""
        if raw.get("product_type") != PRODUCT_TYPE_CAMERA:
            return None
        mac = raw.get("mac")
        model = raw.get("product_model")
        if not mac or not model:
            return None
        return cls(
            mac=str(mac),
            nickname=str(raw.get("nickname") or mac),
            model=str(model),
            firmware=str(raw["firmware_ver"]) if raw.get("firmware_ver") else None,
        )


@dataclass(frozen=True)
class CameraProps:
    """Values from one get_property_list read."""

    power: bool | None = None
    online: bool | None = None
    siren: bool | None = None
    raw_pids: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_property_list(cls, items: list[dict[str, Any]]) -> CameraProps:
        """Parse ``data.property_list``."""
        values: dict[str, Any] = {}
        for item in items or []:
            pid = item.get("pid")
            if pid:
                values[str(pid)] = item.get("value")
        return cls(
            power=_flag(values.get(PID_POWER)),
            online=_flag(values.get(PID_ONLINE)),
            siren=_flag(values.get(PID_SIREN)),
            raw_pids=frozenset(values),
        )
