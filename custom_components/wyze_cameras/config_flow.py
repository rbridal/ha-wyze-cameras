"""Config flow: email, password, API key ID, API key, then a 2FA code if asked."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
import uuid

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_CODE, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    Tokens,
    WyzeAuthError,
    WyzeClient,
    WyzeConnectionError,
    WyzeError,
    WyzeTwoFactorRequired,
    hash_password,
)
from .const import (
    CONF_API_KEY,
    CONF_KEY_ID,
    DATA_ACCESS_TOKEN,
    DATA_PASSWORD_HASH,
    DATA_PHONE_ID,
    DATA_REFRESH_TOKEN,
    DATA_TOKEN_REFRESH_AT,
    DOMAIN,
    OPT_OPTIMISTIC_CAMERAS,
    REQUEST_TIMEOUT,
    TOKEN_REFRESH_AFTER,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _creds_schema(email: str | None = None, key_id: str | None = None) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if email is None:
        fields[vol.Required(CONF_EMAIL)] = TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        )
    fields[vol.Required(CONF_PASSWORD)] = _PASSWORD
    fields[vol.Required(CONF_KEY_ID, default=key_id or vol.UNDEFINED)] = str
    fields[vol.Required(CONF_API_KEY)] = _PASSWORD
    return vol.Schema(fields)


CODE_SCHEMA = vol.Schema({vol.Required(CONF_CODE): str})


class WyzeCamerasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._client: WyzeClient | None = None
        self._data: dict[str, Any] = {}
        self._mfa_method: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WyzeCamerasOptionsFlow:
        """Options: per-camera optimistic state."""
        return WyzeCamerasOptionsFlow()

    def _make_client(self) -> WyzeClient:
        return WyzeClient(
            async_get_clientsession(self.hass),
            email=self._data[CONF_EMAIL],
            password_hash=self._data[DATA_PASSWORD_HASH],
            key_id=self._data[CONF_KEY_ID],
            api_key=self._data[CONF_API_KEY],
            phone_id=self._data[DATA_PHONE_ID],
            user_agent=USER_AGENT,
            refresh_after=TOKEN_REFRESH_AFTER.total_seconds(),
            request_timeout=REQUEST_TIMEOUT,
        )

    async def _login(self) -> tuple[str | None, ConfigFlowResult | None]:
        """Try to log in. Returns (error key, next-step result)."""
        self._client = self._make_client()
        try:
            tokens = await self._client.login()
        except WyzeTwoFactorRequired as err:
            self._mfa_method = err.method
            return None, await self.async_step_verify()
        except WyzeAuthError:
            return "invalid_auth", None
        except WyzeConnectionError:
            return "cannot_connect", None
        except WyzeError:
            _LOGGER.exception("Unexpected Wyze error during login")
            return "unknown", None
        return None, await self._finish(tokens)

    async def _finish(self, tokens: Tokens) -> ConfigFlowResult:
        data = {
            **self._data,
            DATA_ACCESS_TOKEN: tokens.access_token,
            DATA_REFRESH_TOKEN: tokens.refresh_token,
            DATA_TOKEN_REFRESH_AT: tokens.refresh_at,
        }
        if self.source == "reauth":
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)
        return self.async_create_entry(title=f"Wyze ({data[CONF_EMAIL]})", data=data)

    def _collect(self, user_input: dict[str, Any], email: str, phone_id: str) -> None:
        self._data = {
            CONF_EMAIL: email,
            DATA_PASSWORD_HASH: hash_password(user_input[CONF_PASSWORD]),
            CONF_KEY_ID: user_input[CONF_KEY_ID].strip(),
            CONF_API_KEY: user_input[CONF_API_KEY].strip(),
            DATA_PHONE_ID: phone_id,
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Credentials step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()
            self._collect(user_input, email, str(uuid.uuid4()))
            error, result = await self._login()
            if result is not None:
                return result
            errors["base"] = error or "unknown"
        return self.async_show_form(step_id="user", data_schema=_creds_schema(), errors=errors)

    async def async_step_verify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Two-factor code step (TOTP app or SMS)."""
        errors: dict[str, str] = {}
        if user_input is not None and self._client is not None:
            try:
                tokens = await self._client.verify_2fa(user_input[CONF_CODE])
            except WyzeAuthError:
                errors["base"] = "invalid_code"
            except WyzeConnectionError:
                errors["base"] = "cannot_connect"
            except WyzeError:
                _LOGGER.exception("Unexpected Wyze error during 2FA")
                errors["base"] = "unknown"
            else:
                return await self._finish(tokens)
        return self.async_show_form(
            step_id="verify",
            data_schema=CODE_SCHEMA,
            errors=errors,
            description_placeholders={
                "method": "SMS" if self._mfa_method == "sms" else "authenticator app"
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password and API key again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            self._collect(user_input, entry.data[CONF_EMAIL], entry.data[DATA_PHONE_ID])
            error, result = await self._login()
            if result is not None:
                return result
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_creds_schema(email=entry.data[CONF_EMAIL], key_id=entry.data[CONF_KEY_ID]),
            errors=errors,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
        )


class WyzeCamerasOptionsFlow(OptionsFlow):
    """Per-camera 'Optimistic state' (off by default)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick cameras that show commanded state immediately."""
        if user_input is not None:
            return self.async_create_entry(
                data={OPT_OPTIMISTIC_CAMERAS: list(user_input.get(OPT_OPTIMISTIC_CAMERAS, []))}
            )
        options: list[SelectOptionDict] = []
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is not None:
            for mac, cam in sorted(
                runtime.coordinator.cameras.items(), key=lambda kv: kv[1].info.nickname
            ):
                options.append(
                    SelectOptionDict(value=mac, label=f"{cam.info.nickname} ({cam.info.model})")
                )
        current = list(self.config_entry.options.get(OPT_OPTIMISTIC_CAMERAS, []))
        known = {o["value"] for o in options}
        for mac in current:
            if mac not in known:
                options.append(SelectOptionDict(value=mac, label=f"{mac} (not on the account)"))
        schema = vol.Schema(
            {
                vol.Optional(OPT_OPTIMISTIC_CAMERAS, default=current): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
