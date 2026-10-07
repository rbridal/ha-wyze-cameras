# Wyze Cameras for Home Assistant

Power and siren control for Wyze cameras, and nothing else. Every state change
is confirmed by Wyze before Home Assistant shows it.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
![Home Assistant 2026.3+](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-blue)

## Is this integration for you?

Use it if you switch Wyze cameras on and off from automations, for example an
alarm that powers the cameras on when you leave, and you need the switch state
to be the camera's real state.

It is **not** a replacement for the full [Wyze integration](https://github.com/SecKatie/ha-wyzeapi).
There is no live view, no events or clips, no motion or notification toggles,
and no bulbs, plugs, locks or sensors. If you need any of those, keep using
that one.

## At a glance

| | |
|---|---|
| Devices | Wyze cameras on the classic Wyze app API (v3, v4, Pan v2/v3, Cam OG, Duo Doorbell, ...) |
| Per camera | Power switch, siren, online sensor, API response time, last command result |
| Per account | Last / average API response time, API requests today |
| Updates | Cloud polling every 30 s; fast confirmation reads after a command |
| State | Confirmed only, by default. Optional per-camera optimistic mode |
| Auth | Email, password, Wyze API key ID and API key, plus 2FA (authenticator app or SMS) |
| Dependencies | None (own small client on Home Assistant's HTTP session) |
| Minimum HA | 2026.3.0 |

## Before you install

- You need a **Wyze API key**. Create one at
  [developer-api-console.wyze.com](https://developer-api-console.wyze.com/) and
  note both the *API key ID* and the *API key*.
- If two-factor authentication is on for your Wyze account, have your
  authenticator app (or phone, for SMS) ready.
- The domain is `wyze_cameras`, so it can run next to the full Wyze
  integration (`wyzeapi`) while you move over. See
  [Moving from the Wyze (wyzeapi) integration](#moving-from-the-wyze-wyzeapi-integration).

## Install

### HACS

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/rbridal/ha-wyze-cameras`, type **Integration**.
2. Install **Wyze Cameras** and restart Home Assistant.

### Manual

Download `wyze_cameras.zip` from the [latest release](https://github.com/rbridal/ha-wyze-cameras/releases/latest),
unzip it into `config/custom_components/wyze_cameras/`, and restart.

## Set up

**Settings → Devices & services → Add integration → Wyze Cameras**, then enter:

- Email and password. The password is hashed (the triple-MD5 form the Wyze
  login takes) before it is stored; the plain password is never saved.
- API key ID and API key.
- The 2FA code, if Wyze asks for one.

Each camera becomes a device named after its Wyze nickname. With a camera
called *Shop Office Cam* you get `switch.shop_office_cam_power` and
`siren.shop_office_cam_siren`.

If Wyze later rejects the stored login (password changed, API key revoked),
Home Assistant shows a **Reconfigure / re-authenticate** prompt. Enter the
password and API key again (and a 2FA code if asked).

## How state works

**Cameras are security devices, so by default a switch only shows what Wyze
reports.** Nothing is assumed.

When you turn a camera on:

1. The command goes to Wyze. If Wyze rejects it, the action fails with an
   error right away.
2. The switch **keeps showing its last confirmed state** and gets two
   attributes: `pending_command: turn_on` and `pending_since: <time>`.
3. The integration reads that camera again after 1, 1, 1, 2, 3, 5, 8, 13 and
   21 s (55 s in total), on top of the normal 30 s poll.
4. As soon as a read reports the camera on, the switch flips to **on** and the
   pending attributes clear.
5. If nothing confirms it within **60 s**, the switch keeps the real reported
   state, a WARNING is logged, and a `wyze_cameras_command_result` event fires
   with `outcome: timeout`.

Reads that were already on the way when you sent the command are thrown away,
so a slow, out-of-date reply can never flip the switch back. (That was the
cause of the occasional 30 s stall when arming with the full Wyze integration:
the switch went on, a stale poll set it off, and it only came back on with the
next poll.)

The siren works the same way.

### Optimistic state (per camera, off by default)

**Settings → Devices & services → Wyze Cameras → Configure** lets you pick
cameras that show the commanded state immediately. Use it only for cameras
where a moment of "assumed" state is harmless, such as a camera that is off
whenever you are there.

For a selected camera the switch shows the target at once, with the same
`pending_command` / `pending_since` attributes and `optimistic: true`. Stale
reads are discarded the same way. If Wyze has not confirmed within 60 s the
switch **reverts to the reported state** and logs a WARNING, with the same
`timeout` event.

### Trade-offs

| | Confirmed (default) | Optimistic |
|---|---|---|
| What the switch shows | Only what Wyze reported | The command, until confirmed or 60 s pass |
| Lag in the UI | 1–8 s typically (the camera has to boot and report) | None |
| If the command silently fails | Switch never claims it happened | Switch is wrong for up to 60 s, then reverts |
| Automations waiting on the state | Wait for real confirmation | Continue immediately |
| Good for | Alarms, anything security-related | Convenience cameras |

Either way you can see what is happening: the `pending_*` attributes, the
**Last command result** sensor, and the event below.

### Offline cameras

If Wyze reports a camera offline (its "online" property is 0, or Wyze answers
"device offline"), that camera's power switch and siren become
**unavailable** until Wyze reports it online again. The **Online** sensor
stays available and shows *disconnected*.

If the whole Wyze poll fails twice in a row (network or Wyze outage), all
camera entities go unavailable; a single failure is ignored.

## Entities

Per camera (named after the Wyze nickname):

| Entity | Notes |
|---|---|
| `switch.<camera>_power` | Power. Not created for models without a power function (Video Doorbell Pro) |
| `siren.<camera>_siren` | Siren on/off. Not created for models without a siren |
| `binary_sensor.<camera>_online` | Diagnostic. Attributes: `last_read_at`, `last_read_error`, `stale_reads_discarded` |
| `sensor.<camera>_api_response_time` | Diagnostic, ms, latest Wyze request for this camera |
| `sensor.<camera>_last_command_result` | Diagnostic: `confirmed`, `timeout`, `failed`, `offline`, `superseded`. Attributes: command, sent/finished time, `elapsed_s`, reported state, error |

Per account (device *Wyze account*):

| Entity | Notes |
|---|---|
| `sensor.wyze_account_last_api_response_time` | ms |
| `sensor.wyze_account_average_api_response_time` | ms, last 20 answered requests |
| `sensor.wyze_account_api_requests_today` | Resets at local midnight. Attributes: counts by operation and outcome |

## The command result event

Every command ends with one `wyze_cameras_command_result` event:

```yaml
event_type: wyze_cameras_command_result
data:
  entity_id: switch.shop_office_cam_power
  camera: Shop Office Cam
  attribute: power          # or siren
  command: turn_on          # or turn_off
  outcome: confirmed        # confirmed | timeout | failed | offline | superseded
  elapsed_s: 4.0
  reported_state: "on"      # what Wyze last reported
  optimistic: false
```

`superseded` means a newer command for the same camera and attribute replaced
it before it was confirmed.

## Examples

Notify when a camera does not confirm:

```yaml
automation:
  - alias: Wyze camera did not confirm
    triggers:
      - trigger: event
        event_type: wyze_cameras_command_result
        event_data:
          outcome: timeout
    actions:
      - action: notify.notify
        data:
          message: >
            {{ trigger.event.data.camera }} did not confirm
            {{ trigger.event.data.command }} (reported {{ trigger.event.data.reported_state }}).
```

Wait for real confirmation in a script:

```yaml
- action: switch.turn_on
  target:
    entity_id: switch.shop_office_cam_power
- wait_template: "{{ is_state('switch.shop_office_cam_power', 'on') }}"
  timeout: "00:01:00"
```

## API usage

Steady state is one property read per camera every 30 s plus the device list
every 45 minutes. A command adds at most nine reads for that camera, and they
stop as soon as Wyze confirms. A 5-camera account makes about 10 requests a
minute.

## Moving from the Wyze (wyzeapi) integration

Both integrations can be installed at the same time. To end up with the same
entity ids your automations already use:

1. In the old Wyze integration, rename the camera entities you use, e.g.
   `switch.shop_office_cam_power` → `switch.shop_office_cam_power_old`
   (and the sirens). Then disable the old integration entry
   (⋮ → **Disable**). Don't delete it yet.
2. Install **Wyze Cameras** and add it with the same Wyze account.
3. The new entities take the freed ids (`switch.<camera>_power`,
   `siren.<camera>_siren`). If one came up with a `_2` suffix, rename it to the
   exact old id.
4. Set the areas on the new devices.
5. **Device actions and device triggers** that pointed at the old Wyze devices
   do not carry over (new devices). Change them to entity actions, e.g.
   `action: switch.turn_off` with `target: {entity_id: switch.shop_chiller_cam_power}`.
6. If you want optimistic state for some cameras, set it under **Configure**.
7. Test: switch each camera off and on, and check it is really powered and
   recording. Try the siren briefly.
8. When you are happy, delete the old entry.

Entity ids are the thing to keep; unique ids are new (`<MAC>_power`,
`<MAC>_siren`) and do not clash with the old integration's.

## Supported devices

Any camera on the classic Wyze app API should work. Tested against the API
shapes for: Wyze Cam v3 (WYZE_CAKP2JFUS), v4 (HL_CAM4), Pan v2 (HL_PAN2),
Pan v3 (HL_PAN3) and Duo Cam Doorbell (GW_DUO).

Not supported: cameras on Wyze's newer *devicemgmt* API (Pan v4 HL_PAN4,
Floodlight Pro LD_CFP, Battery Cam Pro AN_RSCW, OG GW_GC1). They are skipped
with a warning.

## Troubleshooting

- **"Wyze rejected the email, password, or API key."** Check the API key ID
  and key (they are separate values in the Wyze developer console).
- **Switch shows `pending_command` for a while.** The camera has not reported
  the new state yet. Wyze usually reports power-on once the camera has booted.
- **Last command result is `timeout`.** Wyze accepted the command but never
  reported the new state within 60 s. Check the camera in the Wyze app.
- **Debug logs:** add `custom_components.wyze_cameras: debug` under
  `logger:`. Logs contain operation names, result codes and timings only:
  never request or response bodies, tokens, keys, URLs or MAC addresses.

## Known limitations

- Wyze's app API is private and undocumented. The client mirrors what
  [wyzeapy](https://github.com/SecKatie/wyzeapy) does; Wyze can change it.
- No live view, events, clips, motion or notification toggles, by design.
- Cloud only. A camera that is on but has lost its cloud connection shows unavailable.

## Diagnostics and privacy

**Settings → Devices & services → Wyze Cameras → ⋮ → Download diagnostics**
gives per-camera state, pending commands, last results and API timings.
Email, password hash, API key ID and key, tokens, phone id and nicknames are
redacted, and camera MACs are replaced by a short hash (`cam_3f9a`).

## Removing the integration

Delete the entry under **Settings → Devices & services**, then remove it in
HACS. Nothing is left on the Wyze side.

## Contributing

Issues and pull requests welcome. See [docs/DESIGN.md](docs/DESIGN.md) for
how it works and what is verified.

## License

MIT
