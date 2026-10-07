"""Parsing and model capability tables."""

from __future__ import annotations

from custom_components.wyze_cameras.api.client import hash_password
from custom_components.wyze_cameras.api.models import CameraInfo, CameraProps


def test_hash_password_triple_md5() -> None:
    """Same triple MD5 wyzeapy uses."""
    import hashlib

    once = hashlib.md5(b"pw").hexdigest()
    twice = hashlib.md5(once.encode()).hexdigest()
    assert hash_password("pw") == hashlib.md5(twice.encode()).hexdigest()


def test_camera_info_filters() -> None:
    """Only Camera product_type with mac+model."""
    assert (
        CameraInfo.from_device({"product_type": "Plug", "mac": "x", "product_model": "y"}) is None
    )
    assert (
        CameraInfo.from_device({"product_type": "Camera", "mac": "", "product_model": "y"}) is None
    )
    cam = CameraInfo.from_device(
        {"product_type": "Camera", "mac": "M", "product_model": "HL_CAM4", "nickname": "N"}
    )
    assert cam == CameraInfo("M", "N", "HL_CAM4", None)
    assert cam.has_power and cam.has_siren and cam.supported


def test_capabilities() -> None:
    """Doorbell Pro: no power/siren; v4 pan: devicemgmt (unsupported)."""
    assert not CameraInfo("m", "n", "GW_BE1").has_power
    assert not CameraInfo("m", "n", "GW_BE1").has_siren
    assert not CameraInfo("m", "n", "WYZEC1-JZ").has_siren
    assert not CameraInfo("m", "n", "HL_PAN4").supported
    for model in ("HL_CAM4", "HL_PAN3", "HL_PAN2", "WYZE_CAKP2JFUS", "GW_DUO"):
        info = CameraInfo("m", "n", model)
        assert info.supported and info.has_power and info.has_siren


def test_props_parsing() -> None:
    """ "1"/"0" strings or ints; missing pids stay None."""
    props = CameraProps.from_property_list(
        [{"pid": "P3", "value": "1"}, {"pid": "P5", "value": 0}, {"pid": "P9", "value": "1"}]
    )
    assert props.power is True
    assert props.online is False
    assert props.siren is None
    assert CameraProps.from_property_list([]).power is None
