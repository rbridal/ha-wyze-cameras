"""Constants for the Wyze Cameras integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "wyze_cameras"
MANUFACTURER: Final = "Wyze Labs"
VERSION: Final = "0.1.0"
REPO_URL: Final = "https://github.com/rbridal/ha-wyze-cameras"
USER_AGENT: Final = f"ha-wyze-cameras/{VERSION} (+{REPO_URL})"

# Config entry data keys. The password is never stored: only the triple-MD5
# hash that the Wyze login endpoint takes.
CONF_KEY_ID: Final = "key_id"
CONF_API_KEY: Final = "api_key"
DATA_PASSWORD_HASH: Final = "password_hash"
DATA_ACCESS_TOKEN: Final = "access_token"
DATA_REFRESH_TOKEN: Final = "refresh_token"
DATA_TOKEN_REFRESH_AT: Final = "token_refresh_at"
DATA_PHONE_ID: Final = "phone_id"

# Options
OPT_OPTIMISTIC_CAMERAS: Final = "optimistic_cameras"

# Polling (DESIGN.md section 5)
POLL_INTERVAL: Final = timedelta(seconds=30)
DEVICE_LIST_INTERVAL: Final = timedelta(minutes=45)
# Seconds between confirmation reads after a command (cumulative 1..55 s).
CONFIRM_SCHEDULE: Final = (1, 1, 1, 2, 3, 5, 8, 13, 21)
CONFIRM_WINDOW: Final = timedelta(seconds=60)
# A whole poll must fail this many times in a row before entities go unavailable.
POLL_FAILURES_BEFORE_UNAVAILABLE: Final = 2
REQUEST_TIMEOUT: Final = 15
MAX_PARALLEL_READS: Final = 3
LATENCY_WINDOW: Final = 20
# Access tokens are refreshed proactively after this long (Wyze tokens last ~24 h+).
TOKEN_REFRESH_AFTER: Final = timedelta(hours=23)

EVENT_COMMAND_RESULT: Final = f"{DOMAIN}_command_result"

# Command result outcomes
OUTCOME_CONFIRMED: Final = "confirmed"
OUTCOME_TIMEOUT: Final = "timeout"
OUTCOME_FAILED: Final = "failed"
OUTCOME_OFFLINE: Final = "offline"
OUTCOME_SUPERSEDED: Final = "superseded"

ATTR_PENDING_COMMAND: Final = "pending_command"
ATTR_PENDING_SINCE: Final = "pending_since"
ATTR_OPTIMISTIC: Final = "optimistic"
