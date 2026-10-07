"""Errors raised by the Wyze API client.

Messages never contain response bodies, tokens or URLs: only an operation
name and a Wyze result code.
"""

from __future__ import annotations


class WyzeError(Exception):
    """Base error."""


class WyzeConnectionError(WyzeError):
    """No usable HTTP response (timeout, DNS, TLS, connection reset, bad JSON)."""


class WyzeAuthError(WyzeError):
    """Credentials or tokens were rejected."""


class WyzeTwoFactorRequired(WyzeError):
    """Login needs a verification code (TOTP app or SMS)."""

    def __init__(self, method: str) -> None:
        """Store the 2FA method (``totp`` or ``sms``)."""
        super().__init__(f"two-factor verification required ({method})")
        self.method = method


class WyzeTokenExpired(WyzeError):
    """The access token was rejected (result code 2001)."""


class WyzeDeviceOffline(WyzeError):
    """Wyze answered that the camera is offline (result code 3019)."""


class WyzeApiError(WyzeError):
    """Any other non-success result code."""

    def __init__(self, operation: str, code: str) -> None:
        """Store the operation and Wyze result code."""
        super().__init__(f"{operation} failed with Wyze code {code}")
        self.operation = operation
        self.code = code
