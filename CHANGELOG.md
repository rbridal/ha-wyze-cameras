# Changelog

## 0.1.0 (2026-10-07)

First release.

- Power switch and siren for each Wyze camera on the classic app API.
- Confirmed-only state by default: after a command the entity keeps its last
  confirmed state with `pending_command` / `pending_since` until a Wyze read
  matches, with confirmation reads at 1/1/1/2/3/5/8/13/21 s.
- Reads that started before the latest command, or before a newer accepted
  read, are discarded (fixes the on → off → on flip seen with ha-wyzeapi).
- 60 s confirmation window: on timeout the reported state is kept, a WARNING
  is logged and `wyze_cameras_command_result` fires with `outcome: timeout`.
- Per-camera *Optimistic state* option (off by default), keyed by MAC.
- Diagnostics: online, per-camera API response time, last command result;
  account last/average response time and requests today.
- Config flow with email, password (stored hashed), API key ID/key, TOTP or
  SMS 2FA, and re-authentication. Token refresh keeps every stored field.
- Own minimal client on Home Assistant's shared HTTP session; no wyzeapy.
- Redacted diagnostics; logs never include bodies, tokens, keys, URLs or MACs.
