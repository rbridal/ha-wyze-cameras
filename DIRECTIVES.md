# ha-wyze-cameras — Directives

Home Assistant helpers for Wyze camera power and siren with confirmed state.

## Ownership
- **Masta-Rob** owns this home project DIRECTIVES.md. Flag Rob only for policy changes.

## Safety (always wins)
- No camera cloud credentials in git.
- Siren / power actions need confirmed state; do not fire blindly in loops.

## Directives (priority order, highest first)
- Prefer confirmed state over optimistic UI.
- No voice exposure entities.

## Non-goals
- Not a full Wyze media/NVR replacement (Frigate remains separate; do not change Frigate ports).

## Standing rules that apply here
- No secrets in git. Nothing deployed without Rob's OK / MyHACS approve policy when cataloged.

## Change log
- 2026-11-04 — Masta-Rob — Initial DIRECTIVES.md (Rob's OK: yes, pt1410u).
