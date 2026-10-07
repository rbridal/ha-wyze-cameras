"""Minimal Wyze cloud client: login (+2FA), token refresh, camera list,
camera properties and run_action. Nothing else.

Endpoints and payloads mirror wyzeapy 0.6.1 (``wyze_auth_lib.py`` and
``services/base_service.py``), used read-only as the reference. See
docs/DESIGN.md section 6 for what is verified against what.

Logging rule: never log request or response bodies, tokens, credentials,
URLs or MAC addresses. Only operation names, result codes and timings.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import logging
import time
from typing import Any

import aiohttp

from .errors import (
    WyzeApiError,
    WyzeAuthError,
    WyzeConnectionError,
    WyzeDeviceOffline,
    WyzeTokenExpired,
    WyzeTwoFactorRequired,
)
from .models import CameraInfo, CameraProps

_LOGGER = logging.getLogger(__name__)

AUTH_LOGIN_URL = "https://auth-prod.api.wyze.com/api/user/login"
AUTH_2FA_URL = "https://auth-prod.api.wyze.com/user/login"
AUTH_SMS_URL = "https://auth-prod.api.wyze.com/user/login/sendSmsCode"
REFRESH_URL = "https://api.wyzecam.com/app/user/refresh_token"
OBJECT_LIST_URL = "https://api.wyzecam.com/app/v2/home_page/get_object_list"
PROPERTY_LIST_URL = "https://api.wyzecam.com/app/v2/device/get_property_list"
RUN_ACTION_URL = "https://api.wyzecam.com/app/v2/auto/run_action"

# Public app identifiers the Wyze app API expects (same values as wyzeapy
# const.py). These are not user secrets.
APP_API_KEY = "WMXHYf79Nr5gIlt3r0r7p9Tcw5bvs6BB4U8O8nGJ"
APP_VERSION = "2.18.43"
APP_VER = "com.hualai.WyzeCam___2.18.43"
APP_NAME = "com.hualai.WyzeCam"
PHONE_SYSTEM_TYPE = "1"
SC = "9f275790cab94a72bd206c8876429f3c"
SV = "9d74946e652647e9b6c9d59326aef104"

CODE_SUCCESS = "1"
CODE_PARAMETER_ERROR = "1001"
CODE_TOKEN_ERROR = "2001"
CODE_DEVICE_OFFLINE = "3019"
LOGIN_BAD_CREDENTIALS = 1000

ACTION_POWER_ON = "power_on"
ACTION_POWER_OFF = "power_off"
ACTION_SIREN_ON = "siren_on"
ACTION_SIREN_OFF = "siren_off"

OUTCOME_OK = "ok"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_CONNECTION_ERROR = "connection_error"
OUTCOME_BAD_RESPONSE = "bad_response"
OUTCOME_API_ERROR = "api_error"
OUTCOME_AUTH_ERROR = "auth_error"
OUTCOME_TOKEN_EXPIRED = "token_expired"
OUTCOME_OFFLINE = "offline"
NO_RESPONSE_OUTCOMES = frozenset({OUTCOME_TIMEOUT, OUTCOME_CONNECTION_ERROR})


def hash_password(password: str) -> str:
    """Triple MD5, as the Wyze login endpoint expects (wyzeapy create_password)."""
    value = password
    for _ in range(3):
        value = hashlib.md5(value.encode()).hexdigest()  # noqa: S324
    return value


@dataclass(frozen=True)
class RequestRecord:
    """One timed HTTP request. No bodies, tokens, URLs or device ids."""

    operation: str
    outcome: str
    http_status: int | None
    duration: float
    started: datetime
    camera: str | None = None  # MAC, kept in memory only for per-camera timing


@dataclass
class Tokens:
    """Access/refresh token pair and when to refresh proactively (epoch s)."""

    access_token: str
    refresh_token: str
    refresh_at: float


class WyzeClient:
    """Async client bound to Home Assistant's shared aiohttp session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        email: str,
        password_hash: str,
        key_id: str,
        api_key: str,
        phone_id: str,
        user_agent: str,
        tokens: Tokens | None = None,
        refresh_after: float = 23 * 3600,
        request_timeout: float = 15,
        on_tokens: Callable[[Tokens], Awaitable[None] | None] | None = None,
        observer: Callable[[RequestRecord], None] | None = None,
    ) -> None:
        """Initialize."""
        self._session = session
        self._email = email
        self._password_hash = password_hash
        self._key_id = key_id
        self._api_key = api_key
        self._phone_id = phone_id
        self._user_agent = user_agent
        self.tokens = tokens
        self._refresh_after = refresh_after
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._on_tokens = on_tokens
        self._observer = observer
        self._refresh_lock = asyncio.Lock()
        self._mfa_method: str | None = None
        self._mfa_id: str | None = None

    def set_observer(self, observer: Callable[[RequestRecord], None] | None) -> None:
        """Receive one RequestRecord per HTTP request."""
        self._observer = observer

    # ------------------------------------------------------------------ http

    async def _post(
        self,
        operation: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        camera: str | None = None,
        classify: Callable[[dict[str, Any]], str] | None = None,
    ) -> tuple[int, Any]:
        """POST and parse JSON. Times the request and reports it to the observer.

        ``classify`` maps a parsed body to an outcome (``ok``, ``api_error``,
        ...) so the latency/usage record says what happened.
        """
        started = datetime.now(UTC)
        t0 = time.monotonic()
        status: int | None = None
        outcome = OUTCOME_OK
        hdrs = {"User-Agent": self._user_agent, **(headers or {})}
        try:
            async with self._session.post(
                url, json=json, data=data, headers=hdrs, timeout=self._timeout
            ) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    outcome = OUTCOME_BAD_RESPONSE
                    raise WyzeConnectionError(f"{operation}: response was not JSON") from err
                if not isinstance(body, dict):
                    outcome = OUTCOME_BAD_RESPONSE
                    raise WyzeConnectionError(f"{operation}: unexpected response shape")
                if classify is not None:
                    outcome = classify(body)
                return status, body
        except TimeoutError as err:
            outcome = OUTCOME_TIMEOUT
            raise WyzeConnectionError(f"{operation}: timed out") from err
        except aiohttp.ClientError as err:
            outcome = OUTCOME_CONNECTION_ERROR
            raise WyzeConnectionError(f"{operation}: {type(err).__name__}") from err
        finally:
            self._record(operation, outcome, status, time.monotonic() - t0, started, camera)

    def _record(
        self,
        operation: str,
        outcome: str,
        status: int | None,
        duration: float,
        started: datetime,
        camera: str | None,
    ) -> None:
        if self._observer is not None:
            try:
                self._observer(RequestRecord(operation, outcome, status, duration, started, camera))
            except Exception:  # pragma: no cover - observer bugs must not break calls
                _LOGGER.exception("Request observer failed")

    def _app_payload(self) -> dict[str, Any]:
        return {
            "phone_system_type": PHONE_SYSTEM_TYPE,
            "app_version": APP_VERSION,
            "app_ver": APP_VER,
            "sc": SC,
            "sv": SV,
            "ts": int(time.time()),
            "phone_id": self._phone_id,
            "app_name": APP_NAME,
        }

    @staticmethod
    def _code(body: dict[str, Any]) -> str:
        return str(body.get("code", "")).strip()

    @classmethod
    def _classify_app(cls, body: dict[str, Any]) -> str:
        code = cls._code(body)
        if code == CODE_SUCCESS:
            return OUTCOME_OK
        if code == CODE_TOKEN_ERROR:
            return OUTCOME_TOKEN_EXPIRED
        if code == CODE_DEVICE_OFFLINE:
            return OUTCOME_OFFLINE
        return OUTCOME_API_ERROR

    @staticmethod
    def _classify_auth(body: dict[str, Any]) -> str:
        if body.get("errorCode") is not None:
            return OUTCOME_AUTH_ERROR
        if body.get("access_token") or body.get("mfa_options") or body.get("session_id"):
            return OUTCOME_OK
        return OUTCOME_AUTH_ERROR

    # ------------------------------------------------------------------ auth

    async def _store_tokens(self, access: str, refresh: str) -> None:
        self.tokens = Tokens(access, refresh, time.time() + self._refresh_after)
        if self._on_tokens is not None:
            result = self._on_tokens(self.tokens)
            if asyncio.iscoroutine(result):
                await result

    def _auth_headers(self) -> dict[str, str]:
        return {"keyid": self._key_id, "apikey": self._api_key}

    async def login(self) -> Tokens:
        """Email/password login. Raises WyzeTwoFactorRequired when a code is needed."""
        _, body = await self._post(
            "login",
            AUTH_LOGIN_URL,
            json={"email": self._email, "password": self._password_hash},
            headers=self._auth_headers(),
            classify=self._classify_auth,
        )
        if body.get("errorCode") is not None:
            if body.get("errorCode") == LOGIN_BAD_CREDENTIALS:
                raise WyzeAuthError("login rejected (bad email, password, or API key)")
            raise WyzeAuthError(f"login rejected (Wyze error {body.get('errorCode')})")
        mfa = body.get("mfa_options")
        if mfa:
            if "TotpVerificationCode" in mfa:
                try:
                    self._mfa_id = body["mfa_details"]["totp_apps"][0]["app_id"]
                except (KeyError, IndexError, TypeError) as err:
                    raise WyzeAuthError("login: TOTP details missing") from err
                self._mfa_method = "totp"
                raise WyzeTwoFactorRequired("totp")
            if "PrimaryPhone" in mfa:
                _, sms = await self._post(
                    "login_send_sms",
                    AUTH_SMS_URL,
                    data={
                        "mfaPhoneType": "Primary",
                        "sessionId": body.get("sms_session_id"),
                        "userId": body.get("user_id"),
                    },
                    headers=self._auth_headers(),
                    classify=self._classify_auth,
                )
                self._mfa_id = sms.get("session_id")
                self._mfa_method = "sms"
                raise WyzeTwoFactorRequired("sms")
            raise WyzeAuthError("login: unsupported two-factor method")
        access, refresh = body.get("access_token"), body.get("refresh_token")
        if not access or not refresh:
            raise WyzeAuthError("login: no tokens in response")
        await self._store_tokens(access, refresh)
        assert self.tokens is not None
        return self.tokens

    async def verify_2fa(self, code: str) -> Tokens:
        """Finish login with a TOTP or SMS verification code."""
        if not self._mfa_method or not self._mfa_id:
            raise WyzeAuthError("no two-factor login in progress")
        payload = {
            "email": self._email,
            "password": self._password_hash,
            "mfa_type": "TotpVerificationCode" if self._mfa_method == "totp" else "PrimaryPhone",
            "verification_id": self._mfa_id,
            "verification_code": code.strip(),
        }
        _, body = await self._post(
            "login_2fa",
            AUTH_2FA_URL,
            json=payload,
            headers={"Phone-Id": self._phone_id, "X-API-Key": APP_API_KEY},
            classify=self._classify_auth,
        )
        access, refresh = body.get("access_token"), body.get("refresh_token")
        if not access or not refresh:
            raise WyzeAuthError("verification code rejected")
        await self._store_tokens(access, refresh)
        assert self.tokens is not None
        return self.tokens

    async def refresh(self) -> Tokens:
        """Exchange the refresh token for a new pair."""
        if self.tokens is None:
            raise WyzeAuthError("no refresh token")
        payload = {**self._app_payload(), "refresh_token": self.tokens.refresh_token}
        _, body = await self._post(
            "refresh_token",
            REFRESH_URL,
            json=payload,
            headers={"X-API-Key": APP_API_KEY},
            classify=self._classify_app,
        )
        code = self._code(body)
        data = body.get("data") or {}
        if code != CODE_SUCCESS or not data.get("access_token"):
            raise WyzeAuthError(f"token refresh rejected (Wyze code {code or 'none'})")
        await self._store_tokens(data["access_token"], data.get("refresh_token") or "")
        assert self.tokens is not None
        return self.tokens

    async def _renew(self) -> None:
        """Refresh, falling back to a full login with the stored hash."""
        try:
            await self.refresh()
        except WyzeAuthError:
            _LOGGER.info("Wyze token refresh rejected; logging in again")
            await self.login()

    async def ensure_token(self) -> None:
        """Make sure there is a fresh access token."""
        async with self._refresh_lock:
            if self.tokens is None:
                await self.login()
            elif time.time() >= self.tokens.refresh_at:
                await self._renew()

    # ------------------------------------------------------------- app calls

    async def _app_call(
        self, operation: str, url: str, extra: dict[str, Any], camera: str | None = None
    ) -> dict[str, Any]:
        """Authenticated app-API call with one retry after a token error."""
        await self.ensure_token()
        for attempt in (1, 2):
            assert self.tokens is not None
            token = self.tokens.access_token
            payload = {**self._app_payload(), "access_token": token, **extra}
            _, body = await self._post(
                operation, url, json=payload, camera=camera, classify=self._classify_app
            )
            code = self._code(body)
            if code == CODE_SUCCESS:
                return body
            if code == CODE_TOKEN_ERROR:
                if attempt == 1:
                    async with self._refresh_lock:
                        if self.tokens is not None and self.tokens.access_token == token:
                            await self._renew()
                    continue
                raise WyzeTokenExpired(f"{operation}: token rejected after refresh")
            if code == CODE_DEVICE_OFFLINE:
                raise WyzeDeviceOffline(f"{operation}: camera offline")
            raise WyzeApiError(operation, code or "none")
        raise WyzeTokenExpired(operation)  # pragma: no cover

    async def get_cameras(self) -> list[CameraInfo]:
        """Every camera on the account (other device types are ignored)."""
        body = await self._app_call("get_object_list", OBJECT_LIST_URL, {})
        devices = (body.get("data") or {}).get("device_list") or []
        cameras = []
        for raw in devices:
            if isinstance(raw, dict) and (cam := CameraInfo.from_device(raw)) is not None:
                cameras.append(cam)
        return cameras

    async def get_properties(self, camera: CameraInfo) -> CameraProps:
        """Read power, online and siren for one camera."""
        body = await self._app_call(
            "get_property_list",
            PROPERTY_LIST_URL,
            {"device_model": camera.model, "device_mac": camera.mac, "target_pid_list": []},
            camera=camera.mac,
        )
        items = (body.get("data") or {}).get("property_list") or []
        return CameraProps.from_property_list(items)

    async def run_action(self, camera: CameraInfo, action: str) -> None:
        """Send power_on / power_off / siren_on / siren_off."""
        await self._app_call(
            f"run_action/{action}",
            RUN_ACTION_URL,
            {
                "provider_key": camera.model,
                "instance_id": camera.mac,
                "action_key": action,
                "action_params": {},
                "custom_string": "",
            },
            camera=camera.mac,
        )
