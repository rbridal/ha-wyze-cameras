"""Shared fixtures: FakeWyzeCloud (in-memory Wyze account) and a set-up entry."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import time
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.wyze_cameras.api.client import (
    AUTH_2FA_URL,
    AUTH_LOGIN_URL,
    AUTH_SMS_URL,
    OBJECT_LIST_URL,
    PROPERTY_LIST_URL,
    REFRESH_URL,
    RUN_ACTION_URL,
    hash_password,
)
from custom_components.wyze_cameras.const import (
    CONF_API_KEY,
    CONF_KEY_ID,
    DATA_ACCESS_TOKEN,
    DATA_PASSWORD_HASH,
    DATA_PHONE_ID,
    DATA_REFRESH_TOKEN,
    DATA_TOKEN_REFRESH_AT,
    DOMAIN,
    OPT_OPTIMISTIC_CAMERAS,
)

EMAIL = "rob@example.com"
PASSWORD = "correct horse battery staple"
KEY_ID = "key-id-0123456789"
API_KEY = "api-key-secret-abcdefghijklmnopqrstuvwxyz"
PHONE_ID = "11111111-2222-3333-4444-555555555555"
TOTP_CODE = "123456"

OFFICE = "AAAAAA000001"
CHILLER = "AAAAAA000002"
PARKING = "AAAAAA000003"
PAN4 = "AAAAAA000004"
PLUG = "AAAAAA000005"

OFFICE_POWER = "switch.shop_office_cam_power"
OFFICE_SIREN = "siren.shop_office_cam_siren"
CHILLER_POWER = "switch.shop_chiller_cam_power"
PARKING_POWER = "switch.shop_parking_cam_power"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@dataclass
class FakeCamera:
    """One camera as the fake cloud sees it."""

    mac: str
    nickname: str
    model: str
    product_type: str = "Camera"
    power: bool = False
    siren: bool = False
    online: bool = True
    # Seconds between an accepted command and the property read reflecting it
    # (a camera booting before it reports P3=1).
    power_delay: float = 0.0
    siren_delay: float = 0.0
    ignore_actions: bool = False
    read_code: str = "1"
    action_code: str = "1"
    _scheduled: dict[str, tuple[bool, datetime]] = field(default_factory=dict)

    def settle(self, now: datetime) -> None:
        """Apply scheduled state changes that are due."""
        for name, (value, at) in list(self._scheduled.items()):
            if now >= at:
                setattr(self, name, value)
                del self._scheduled[name]

    def schedule(self, name: str, value: bool, delay: float, now: datetime) -> None:
        """Change ``name`` after ``delay`` seconds."""
        self._scheduled[name] = (value, now + timedelta(seconds=delay))
        self.settle(now)

    def device_record(self) -> dict[str, Any]:
        """get_object_list record."""
        return {
            "mac": self.mac,
            "nickname": self.nickname,
            "product_model": self.model,
            "product_type": self.product_type,
            "firmware_ver": "4.52.9.1234",
            "device_params": {"ip": "192.168.1.50", "ssid": "ShopWiFi"},
        }


class FakeWyzeCloud:
    """In-memory Wyze account behind the mocked aiohttp session."""

    def __init__(self) -> None:
        """Initialize."""
        self.cameras: dict[str, FakeCamera] = {
            OFFICE: FakeCamera(OFFICE, "Shop Office Cam", "HL_CAM4"),
            CHILLER: FakeCamera(CHILLER, "Shop Chiller Cam", "HL_PAN2"),
            PARKING: FakeCamera(PARKING, "Shop Parking Cam", "WYZE_CAKP2JFUS"),
            PAN4: FakeCamera(PAN4, "Pan v4", "HL_PAN4"),
            PLUG: FakeCamera(PLUG, "Shop Plug", "WLPP1CFH", product_type="Plug"),
        }
        self.mfa: str | None = None  # None, "totp" or "sms"
        self.token_n = 1
        self.valid_access = {"access-1"}
        self.valid_refresh = {"refresh-1"}
        self.refresh_ok = True
        self.fail_all: Exception | None = None
        self.calls: list[tuple[str, str | None]] = []
        self.headers: list[dict[str, str]] = []
        self.hold_reads: dict[str, asyncio.Event] = {}
        self.held = 0

    # ------------------------------------------------------------- helpers

    def reads(self, mac: str | None = None) -> int:
        """Number of get_property_list calls (optionally for one camera)."""
        return sum(1 for op, m in self.calls if op == "get_property_list" and mac in (None, m))

    def actions(self, mac: str | None = None) -> list[str]:
        """run_action keys sent (optionally for one camera)."""
        return [
            op.split("/", 1)[1]
            for op, m in self.calls
            if op.startswith("run_action/") and mac in (None, m)
        ]

    def _new_tokens(self) -> tuple[str, str]:
        self.token_n += 1
        access, refresh = f"access-{self.token_n}", f"refresh-{self.token_n}"
        self.valid_access = {access}
        self.valid_refresh = {refresh}
        return access, refresh

    @staticmethod
    def _reply(body: dict[str, Any], status: int = 200) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse("post", URL("https://fake"), status=status, json=body)

    # ------------------------------------------------------------ handlers

    async def handle(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        """Dispatch one request."""
        if self.fail_all is not None:
            return AiohttpClientMockResponse("post", url, exc=self.fail_all)
        data = dict(data or {})
        now = dt_util.utcnow()
        path = str(url)
        if path == AUTH_LOGIN_URL:
            return self._login(data)
        if path == AUTH_SMS_URL:
            self.calls.append(("send_sms", None))
            return self._reply({"session_id": "sms-session-2"})
        if path == AUTH_2FA_URL:
            self.calls.append(("login_2fa", None))
            if data.get("verification_code") != TOTP_CODE:
                return self._reply({"errorCode": 1001, "description": "bad code"}, 400)
            expected = "app-totp-1" if self.mfa == "totp" else "sms-session-2"
            assert data.get("verification_id") == expected
            access, refresh = self._new_tokens()
            return self._reply({"access_token": access, "refresh_token": refresh})
        if path == REFRESH_URL:
            self.calls.append(("refresh_token", None))
            if not self.refresh_ok or data.get("refresh_token") not in self.valid_refresh:
                return self._reply({"code": "2001", "msg": "refresh token error"})
            access, refresh = self._new_tokens()
            return self._reply(
                {"code": "1", "data": {"access_token": access, "refresh_token": refresh}}
            )
        if data.get("access_token") not in self.valid_access:
            self.calls.append(("token_error", None))
            return self._reply({"code": "2001", "msg": "AccessTokenError"})
        if path == OBJECT_LIST_URL:
            self.calls.append(("get_object_list", None))
            return self._reply(
                {
                    "code": "1",
                    "data": {"device_list": [c.device_record() for c in self.cameras.values()]},
                }
            )
        if path == PROPERTY_LIST_URL:
            mac = data["device_mac"]
            cam = self.cameras[mac]
            assert data["device_model"] == cam.model
            self.calls.append(("get_property_list", mac))
            cam.settle(now)
            # Snapshot at request time: a held read returns what was true when
            # it started (that is what makes it stale).
            if cam.read_code != "1":
                body = {"code": cam.read_code, "msg": "device offline"}
            else:
                body = {
                    "code": "1",
                    "data": {
                        "property_list": [
                            {"pid": "P3", "value": "1" if cam.power else "0"},
                            {"pid": "P5", "value": "1" if cam.online else "0"},
                            {"pid": "P1049", "value": "1" if cam.siren else "0"},
                        ]
                    },
                }
            if (gate := self.hold_reads.pop(mac, None)) is not None:  # one-shot
                self.held += 1
                await gate.wait()
                self.held -= 1
            return self._reply(body)
        if path == RUN_ACTION_URL:
            mac = data["instance_id"]
            cam = self.cameras[mac]
            assert data["provider_key"] == cam.model
            action = data["action_key"]
            self.calls.append((f"run_action/{action}", mac))
            if cam.action_code != "1":
                return self._reply({"code": cam.action_code, "msg": "error"})
            if not cam.ignore_actions:
                name, value = action.split("_")
                delay = cam.power_delay if name == "power" else cam.siren_delay
                cam.schedule(name, value == "on", delay, now)
            return self._reply({"code": "1", "data": {}})
        raise AssertionError("unexpected URL")

    def _login(self, data: dict[str, Any]) -> AiohttpClientMockResponse:
        self.calls.append(("login", None))
        if str(data.get("email")).lower() != EMAIL or data.get("password") != hash_password(
            PASSWORD
        ):
            return self._reply({"errorCode": 1000, "description": "bad credentials"}, 400)
        hdrs = self.headers[-1]
        if hdrs.get("keyid") != KEY_ID or hdrs.get("apikey") != API_KEY:
            return self._reply({"errorCode": 1000, "description": "bad api key"}, 400)
        if self.mfa == "totp":
            return self._reply(
                {
                    "mfa_options": ["TotpVerificationCode"],
                    "mfa_details": {"totp_apps": [{"app_id": "app-totp-1"}]},
                    "user_id": "user-1",
                }
            )
        if self.mfa == "sms":
            return self._reply(
                {"mfa_options": ["PrimaryPhone"], "sms_session_id": "sms-1", "user_id": "user-1"}
            )
        access, refresh = self._new_tokens()
        return self._reply({"access_token": access, "refresh_token": refresh})


@pytest.fixture
def cloud(aioclient_mock: AiohttpClientMocker) -> Generator[FakeWyzeCloud]:
    """Register the fake cloud on the mocked aiohttp session."""
    fake = FakeWyzeCloud()
    original = aioclient_mock.match_request

    async def match_request(method, url, *, headers=None, **kwargs):  # type: ignore[no-untyped-def]
        fake.headers.append(dict(headers or {}))
        return await original(method, url, headers=headers, **kwargs)

    aioclient_mock.match_request = match_request  # type: ignore[method-assign]
    for url in (
        AUTH_LOGIN_URL,
        AUTH_SMS_URL,
        AUTH_2FA_URL,
        REFRESH_URL,
        OBJECT_LIST_URL,
        PROPERTY_LIST_URL,
        RUN_ACTION_URL,
    ):
        aioclient_mock.post(url, side_effect=fake.handle)
    yield fake


def entry_data(**overrides: Any) -> dict[str, Any]:
    """Stored entry data with valid tokens."""
    return {
        "email": EMAIL,
        DATA_PASSWORD_HASH: hash_password(PASSWORD),
        CONF_KEY_ID: KEY_ID,
        CONF_API_KEY: API_KEY,
        DATA_PHONE_ID: PHONE_ID,
        DATA_ACCESS_TOKEN: "access-1",
        DATA_REFRESH_TOKEN: "refresh-1",
        DATA_TOKEN_REFRESH_AT: time.time() + 23 * 3600,
        **overrides,
    }


def make_entry(optimistic: list[str] | None = None, **data: Any) -> MockConfigEntry:
    """A config entry for the fake account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Wyze ({EMAIL})",
        unique_id=EMAIL,
        version=1,
        minor_version=1,
        data=entry_data(**data),
        options={OPT_OPTIMISTIC_CAMERAS: optimistic or []},
    )


@pytest.fixture
def optimistic() -> list[str]:
    """Override per test to opt cameras in."""
    return []


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, cloud: FakeWyzeCloud, optimistic: list[str]
) -> MockConfigEntry:
    """A loaded entry backed by the fake cloud."""
    entry = make_entry(optimistic)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float, step: float = 1.0
) -> None:
    """Move frozen time forward in steps, firing timers as we go."""
    remaining = seconds
    while remaining > 1e-9:
        delta = min(step, remaining)
        freezer.tick(timedelta(seconds=delta))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        remaining -= delta


async def spin(predicate: Callable[[], bool], rounds: int = 200) -> None:
    """Yield to the loop until ``predicate`` holds (no block_till_done: a read may be held)."""
    for _ in range(rounds):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


class StateRecorder:
    """Records each distinct state an entity shows."""

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        """Start recording."""
        self.states: list[str] = [hass.states.get(entity_id).state]

        @callback
        def _changed(event: Event) -> None:
            new = event.data["new_state"]
            if new is not None and new.state != self.states[-1]:
                self.states.append(new.state)

        self.unsub = async_track_state_change_event(hass, [entity_id], _changed)


def capture_events(hass: HomeAssistant, event_type: str) -> list[dict[str, Any]]:
    """Collect event data for ``event_type``."""
    events: list[dict[str, Any]] = []

    @callback
    def _listener(event: Event) -> None:
        events.append(dict(event.data))

    hass.bus.async_listen(event_type, _listener)
    return events


CONNECTION_ERROR = aiohttp.ClientConnectionError("boom")
