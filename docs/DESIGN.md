# Design

## 1. Scope

Wyze cameras only: power and siren, plus diagnostics. Replaces the parts of
SecKatie/ha-wyzeapi that were actually used on Rob's Shop and Home HA
(power switches and sirens; no camera entity, motion/notification switch,
event or clip was referenced anywhere).

Deliberately out of scope: camera/stream entities, WebRTC, events, clips,
motion and notification switches, other Wyze device types, the devicemgmt
API, BLE.

## 2. Why a new integration and domain

- `wyze_cameras` is a new domain so it can run next to `wyzeapi` during the
  cutover and stays eligible for HACS default (HACS rejects a second
  integration on an existing domain; toshiba_ac_community precedent).
- Fresh, non-fork repo (rbridal/ha-wyze-cameras). The old fork
  (rbridal/ha-wyzeapi) is left as is until cutover.
- Own ~450-line client instead of wyzeapy: wyzeapy builds its own
  `ClientSession` (blocking SSL setup in the event loop), has a global
  UpdateManager whose callbacks overwrite entity state, and its previous
  versions logged response bodies that included signed stream credentials.

## 3. Root cause of the slow arm-away (re-derived, not carried over)

Verified from Shop history (2026-10-01..07), automation traces and the
ha-wyzeapi 0.1.40 + wyzeapy 0.6.1 code:

- Normal arm-away takes 3–9 s. Two outliers (10-02 22:33, 10-07 03:11)
  took ~34–36 s. In both, one camera's power switch went on → off → on, and
  the second "on" arrived ~30 s later; *Done Arming* fired 1 ms after it.
- The power commands themselves were fast (shop_occupy_end finished in
  0.81 s; all switches showed on at once).
- ha-wyzeapi's `WyzeSwitch.async_update_callback` replaces the device with
  whatever wyzeapy's UpdateManager last read (each device ~every 30 s, one
  device per second). A read in flight during the command, or taken before
  the camera boots and reports P3=1, overwrote the optimistic "on". The
  `_just_updated` guard only covers `async_update`, which never runs. Nothing
  re-reads quickly, so the group `switch.shop_cams` (all: true) waited for
  the next poll.
- Event lists and clips play no part. The camera records continuously to SD
  while powered; "video verification" means the camera is powered and
  recording locally. The unreleased `shop-no-clips` branch only made polls
  shorter.

## 4. State model

Per camera and attribute (power, siren): `confirmed` value plus at most one
`Pending(target, since, seq)`.

- Every read and every command takes a number from one monotonic sequence.
- A command sets `last_command_seq` **before** it is sent.
- A read result is accepted only if its sequence number is ≥
  `last_command_seq` and ≥ the last accepted read's. Otherwise it is
  discarded and counted (`stale_reads_discarded`).
- Accepted reads update `confirmed` (and online). A pending command whose
  target equals `confirmed` resolves as `confirmed` immediately.
- After a successful send: a 60 s timer and a confirmation schedule of reads
  at +1, +1, +1, +2, +3, +5, +8, +13, +21 s (cumulative 55 s). The schedule
  stops as soon as nothing is pending.
- Timeout: pending cleared, entity shows `confirmed`, WARNING logged, event
  `wyze_cameras_command_result` with `outcome: timeout`.
- A new command for the same attribute supersedes the pending one
  (`outcome: superseded`).
- Displayed value: `confirmed`, or `pending.target` when the camera is in the
  per-camera optimistic set (options flow, keyed by MAC, default empty).
- Failures: Wyze 3019 on a command → `offline` + error; other codes →
  `failed` + error; auth failure → `failed` + reauth flow.

The tests in `tests/test_race.py` reproduce the 03:11 sequence (poll in
flight, power_on, stale P3=0 landing before and after confirmation) and
fail if the sequence check is removed.

## 5. Polling and availability

- 30 s coordinator poll: device list when due (startup, then every 45 min),
  then one `get_property_list` per camera, at most 3 in parallel.
- A camera that drops off the account is unavailable; a new camera triggers
  an entry reload.
- Power/siren entities are unavailable while Wyze reports the camera offline
  (P5=0 or code 3019), as ha-wyzeapi did. The Online sensor stays available.
- One failed poll keeps the last states; two in a row → all camera entities
  unavailable (`UpdateFailed`).

## 6. Wyze API (what is verified against what)

Reference: wyzeapy 0.6.1 (`wyze_auth_lib.py`, `services/base_service.py`,
`services/camera_service.py`, `const.py`, `types.py`, `utils.py`) and
SecKatie/ha-wyzeapi master (`switch.py`, `siren.py`), read-only. **No live
authenticated call was made while building 0.1.0.**

| Item | Value used | Status |
|---|---|---|
| Login | POST `auth-prod.api.wyze.com/api/user/login`, json `{email, password: md5(md5(md5(pw)))}`, headers `keyid`, `apikey` | Matches wyzeapy |
| Bad credentials | `errorCode` 1000 | Matches wyzeapy |
| TOTP | `mfa_options` contains `TotpVerificationCode`; `verification_id = mfa_details.totp_apps[0].app_id` | Matches wyzeapy |
| SMS | `PrimaryPhone` → POST `/user/login/sendSmsCode` form `{mfaPhoneType: Primary, sessionId: sms_session_id, userId}` → `session_id` | Matches wyzeapy; never exercised by Rob |
| 2FA verify | POST `auth-prod.api.wyze.com/user/login`, headers `Phone-Id`, `X-API-Key` (app key), json `{email, password, mfa_type, verification_id, verification_code}` | Matches wyzeapy except User-Agent (below) |
| Refresh | POST `api.wyzecam.com/app/user/refresh_token`, header `X-API-Key`, app fields + `refresh_token`; reply `data.access_token/refresh_token` | Matches wyzeapy |
| App payload | `phone_system_type "1"`, `app_version "2.18.43"`, `app_ver`, `sc`, `sv`, `ts`, `phone_id`, `app_name`, `access_token` | Matches wyzeapy const |
| Device list | POST `/app/v2/home_page/get_object_list` → `data.device_list[]` (`mac`, `nickname`, `product_model`, `product_type`) | Matches wyzeapy |
| Firmware | `device_list[].firmware_ver` → device sw_version | **Unverified** (wyzeapy and ha-wyzeapi don't read it; harmless if absent) |
| Properties | POST `/app/v2/device/get_property_list` `{device_model, device_mac, target_pid_list: []}` → `data.property_list[{pid, value}]` | Matches wyzeapy |
| PIDs | P3 power, P5 online, P1049 siren; `"1"` = true | Matches wyzeapy `PropertyIDs` and camera update |
| Actions | POST `/app/v2/auto/run_action` `{provider_key: model, instance_id: mac, action_key: power_on/power_off/siren_on/siren_off, action_params: {}, custom_string: ""}` | Matches wyzeapy |
| Result codes | `"1"` ok, `"2001"` token error (refresh + one retry), `"3019"` device offline, other → error | Codes match wyzeapy; wyzeapy silently ignores 3019, here it marks the camera offline / fails the command |
| Model tables | No siren: WYZECP1_JEF, WYZEC1-JZ, GW_BE1. No power: GW_BE1. devicemgmt (skipped): LD_CFP, AN_RSCW, GW_GC1, HL_PAN4 | Match ha-wyzeapi / wyzeapy |
| HL_CAM4, HL_PAN3, HL_PAN2, WYZE_CAKP2JFUS | classic API, power + siren | Code path matches; these were working with ha-wyzeapi on Shop |
| GW_DUO | classic API, power + siren | Code path matches ha-wyzeapi; **hardware behaviour unverified** |
| P5 on doorbells | online flag | **Unverified** for GW_DUO |
| User-Agent | `ha-wyze-cameras/0.1.0 (+https://github.com/rbridal/ha-wyze-cameras)` on every request | **Unverified**: wyzeapy sends `wyzeapy` on login and `wyze_android_2.19.14` on 2FA verify |
| Token lifetime | proactive refresh after 23 h | From wyzeapy's refresh_time; not measured |
| TLS | Home Assistant's default client context | api.wyzecam.com and auth-prod.api.wyze.com verified with it from the build box; wyzeapy's extra DigiCert Global Root CA not needed there |

## 7. Privacy

- Stored: email, password **hash**, API key ID/key, tokens, a random
  phone id. Never the plain password.
- Logs: operation names, Wyze result codes, timings and camera nicknames only.
  Never request/response bodies, tokens, keys, URLs or MACs (tested).
- Diagnostics: redacts email, hash, keys, tokens, phone id and nicknames;
  MACs become `cam_xxxx` hashes (tested).

## 8. Testing

`pytest` with pytest-homeassistant-custom-component against a fake Wyze
cloud (`tests/conftest.py`) that models boot delay, ignored commands,
offline codes, token rotation, 2FA and held (in-flight) reads. CI runs the
suite on Home Assistant 2026.9.x and the minimum 2026.3.x, plus ruff,
hassfest and HACS validation.
