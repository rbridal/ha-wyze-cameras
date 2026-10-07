"""Config flow: credentials, TOTP/SMS 2FA, errors, reauth."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.wyze_cameras.api.client import hash_password
from custom_components.wyze_cameras.const import (
    DATA_ACCESS_TOKEN,
    DATA_PASSWORD_HASH,
    DATA_PHONE_ID,
    DOMAIN,
)

from .conftest import (
    API_KEY,
    CONNECTION_ERROR,
    EMAIL,
    KEY_ID,
    PASSWORD,
    PHONE_ID,
    TOTP_CODE,
    FakeWyzeCloud,
    make_entry,
)

USER_INPUT = {"email": EMAIL, "password": PASSWORD, "key_id": KEY_ID, "api_key": API_KEY}


async def _start(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow_no_2fa(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """Creates the entry with the password hash only (never the password)."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "email": "  Rob@Example.com "}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wyze (Rob@Example.com)"
    entry = result["result"]
    assert entry.unique_id == "rob@example.com"
    data = entry.data
    assert "password" not in data
    assert PASSWORD not in str(data)
    assert data[DATA_PASSWORD_HASH] == hash_password(PASSWORD)
    assert data[DATA_ACCESS_TOKEN].startswith("access-")
    assert len(data[DATA_PHONE_ID]) == 36


async def test_user_flow_totp(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """TOTP: verify step, wrong code shows an error, right code creates the entry."""
    cloud.mfa = "totp"
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "verify"
    assert result["description_placeholders"] == {"method": "authenticator app"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "000000"})
    assert result["errors"] == {"base": "invalid_code"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": f" {TOTP_CODE} "}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_sms(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """SMS: the code is requested, then verified with the SMS session id."""
    cloud.mfa = "sms"
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["step_id"] == "verify"
    assert result["description_placeholders"] == {"method": "SMS"}
    assert ("send_sms", None) in cloud.calls
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": TOTP_CODE})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"password": "wrong"}, "invalid_auth"),
        ({"api_key": "wrong"}, "invalid_auth"),
    ],
)
async def test_user_flow_invalid_auth(
    hass: HomeAssistant, cloud: FakeWyzeCloud, change: dict, error: str
) -> None:
    """Bad password or API key -> invalid_auth, and the form can be retried."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, **change}
    )
    assert result["errors"] == {"base": error}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """Network error -> cannot_connect."""
    cloud.fail_all = CONNECTION_ERROR
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """One entry per account."""
    make_entry().add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """Reauth keeps email and phone id, replaces hash/keys/tokens, reloads."""
    entry = make_entry(password_hash="stale", api_key="old-key", access_token="dead")
    entry.add_to_hass(hass)
    cloud.mfa = "totp"
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": PASSWORD, "key_id": KEY_ID, "api_key": API_KEY}
    )
    assert result["step_id"] == "verify"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": TOTP_CODE})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["email"] == EMAIL
    assert entry.data[DATA_PHONE_ID] == PHONE_ID
    assert entry.data[DATA_PASSWORD_HASH] == hash_password(PASSWORD)
    assert entry.data["api_key"] == API_KEY
    assert entry.data[DATA_ACCESS_TOKEN] != "dead"


async def test_reauth_bad_password(hass: HomeAssistant, cloud: FakeWyzeCloud) -> None:
    """Wrong password in reauth -> error on the form."""
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "nope", "key_id": KEY_ID, "api_key": API_KEY}
    )
    assert result["errors"] == {"base": "invalid_auth"}
