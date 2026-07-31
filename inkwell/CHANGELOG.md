# Changelog

## 0.12.2

- Bake a default CJK font (LXGW WenKai Screen, OFL) into the image at build time, so a fresh
  install renders the seeded template out of the box. User-uploaded fonts in `/config/fonts`
  still take precedence.

## 0.12.1

- Fix: a fresh boot (power-on / reset / flash) now forces a repaint — a firmware update that
  changes rendering with no content change no longer leaves a stale image on the panel.
- Fix: `clean up unused` / `unused-helpers` now refuse (409) instead of deleting when any display
  config fails to load, so a transiently-unparseable display can't have its helpers deleted as
  false orphans.
- Fix: correct the add-on repository URL (`rootn9633/inkwell`).

## 0.12.0

- **Change detection — skip needless refreshes.** `GET /devices/<id>/status` now returns `rev`, a
  32-bit content revision derived from the render hash (updated only when the framebuffer actually
  changes). The firmware holds the last-painted rev in RTC and **downloads + repaints only when it
  changes** — so keep-awake mode no longer flashes the e-ink every cycle, and a normal wake with no
  changes skips the framebuffer download and the panel refresh entirely (less power, longer panel life).

## 0.11.0

- **Per-device keep-awake.** A tokenless `GET /devices/<id>/status` returns `{keep_awake}` from a
  per-device inkwell-**owned** `input_boolean` (created/toggled from the editor's Firmware tab). The
  helper is watched for state but never triggers a re-render, and is cleanup-safe (counts as
  referenced). While on, the device skips deep sleep and refreshes on a short loop; off, it resumes
  normal sleep.
- **Firmware rework to the field-proven model.** Generated firmware now downloads the raw `.bin`
  framebuffer via `esp_http_client` (no `online_image`/PNG decode), keeps a power bank alive with
  ~20s wakes + an RTC `wake_count` throttle (real refresh every `refresh_interval`), forces a refresh
  on a fresh boot (power-on/reset), and genuinely stays awake (`deep_sleep.prevent` + 30s loop) while
  keep-awake is on. Timings overridable via a `device:` block; validated against `esphome config`.
- **White-background (WYSIWYG) convention.** The bundled Waveshare profile renders white-on-black
  (`bg_color: 255`), and firmware draws the framebuffer straight (no inversion), so the editor
  preview matches the panel. `invert: true` on a hardware profile flips B/W if a future ESPHome
  driver change swaps them.

## 0.10.0

- **ESPHome firmware generation.** `GET /api/displays/<name>/firmware` returns a ready-to-flash
  `display-<name>.yaml` — wake from deep sleep, fetch the rendered PNG over HTTP, paint, sleep.
  Wiring (board, SPI, pins, panel model, rotation, resize) comes from the hardware profile's
  `esphome:` block; validated against the official `esphome config`.
- New add-on option **`renderer_url`** — the host address ESP32 screens fetch from (e.g.
  `http://192.168.0.139:5123`); one value per install, set once. Generated firmware uses it, or a
  placeholder if left blank. Per-display refresh cadence via an optional `device: { sleep_duration }`
  block in the display config.

## 0.9.3

- **Helper lifecycle — inkwell as source of truth.** An ownership ledger (`/data/helpers.json`)
  is the sole authority for which helpers inkwell may change; it projects config → HA one way
  (existence, options, name) and never touches a helper's value or area. Ledger loss degrades
  to create-only.
- **Status-driven Helpers panel** with explicit actions: **Create** missing, **Sync** owned
  `input_select` options to match the config (value preserved; warns if the current value is
  dropped), **Adopt** a referenced `.storage` helper into the ledger (YAML helpers can't be
  adopted — shown as `external (YAML)`), and **Clean up unused** (deletes only owned helpers no
  longer referenced by any display).
- `ha_ws` gained `update_helper` / `delete_helper` / `list_storage_helpers`. Fixed:
  `input_select/update` requires a `name` key (Sync would otherwise always fail).

## 0.9.2

- Builder polish: option-set and inline choice options render as distinct removable pills;
  a select-equals condition shows each select as a mini-chip with a labeled "+ select";
  toggle chips use short entity ids; and item rows are distinct tiles so each delete
  control clearly belongs to its row.
- Migration: a simple two-variant item (toggle on / else) is split into two Lines
  (e.g. 蜂蜜柚子茶 / 蜂蜜柚子氣泡); the Choice separator is split out of the old prefix; and
  Advanced items show their variant text instead of a blank label.

## 0.9.1

- Legacy migration now recovers Choice options from the live `input_select` entities in HA
  (the old config didn't store them) and rebuilds the shared `bottles` option set.
- Choice items support inline options in the builder (not just a named set), and
  `/api/entities` returns each `input_select`'s options.

## 0.9.0

- Menu builder UI: a structured editor for the new schema — shared **option sets**, a
  **layout** section, and **groups** of **items** with composable `when` conditions
  (toggle on / toggle off / select-equals) and Choice items that reference an option set.
- **Helpers panel**: lists the managed `input_boolean`/`input_select` a menu references that
  are missing from HA, with a one-click **Create missing helpers** button.
- One-click **migration** of legacy `sections` menus to the new `groups` format (renders
  identically). The former "YAML" tab is now **Advanced**.

## 0.8.0

- Helper auto-creation (backend): detect the managed `input_boolean`/`input_select`
  entities a display references that are missing from HA, and create them over the
  Supervisor websocket (`input_boolean/create`, `input_select/create` with options
  resolved from the display's option sets). New endpoints `GET
  /api/displays/<name>/missing-helpers` and `POST /api/displays/<name>/create-helpers`.
  Surfaced in the builder UI in the next release.

## 0.7.0

- Menu renderer: new `groups` / `items` / `when` schema — shared `option_sets`, per-item
  render (line / choice / advanced) and composable `when` conditions (`entity_on`,
  `entity_off`, `select_equals`), and `hide_if_empty` groups. The bundled `tea_menu`
  template is rewritten to it (byte-identical output).
- The legacy `sections` schema still renders via a compatibility path, so existing display
  configs keep working unchanged.

## 0.6.0

- Structured menu **section editor** in the Form tab: add / remove / reorder sections
  (bottles, static, conditional) and their items, with the entity picker wired into item
  fields and a simple "show when entity on" condition — no YAML needed for common edits.
- Advanced items (`variants`, `any_select_equals`) and dashboard cards are preserved and
  flagged as "edit in YAML"; the structured editor never drops fields it doesn't manage.

## 0.5.0

- Editing UI: create / duplicate / rename / delete displays; a settings form (hardware,
  renderer, fonts) and a validated YAML editor for everything else, with a "Render now"
  button, and a searchable HA entity picker that inserts entity ids.
- Live preview: the editor re-renders the current *unsaved* config as you type (debounced,
  via `POST /api/preview`), so edits show immediately; Save then persists + re-renders.
- Unsaved-changes indicator: the editor flags unsaved edits, disables Save until there are
  changes, and warns before navigating away or closing the tab.
- Serve UI assets with `Cache-Control: no-cache` so the ingress iframe revalidates and
  always picks up updates instead of a stale cached bundle.
- Font upload: drop a `.ttf`/`.otf` into `/config/fonts` from the browser.
- New ingress API: display CRUD, `/api/entities`, `/api/hardware`, `/api/renderers`,
  `/api/fonts` (list + upload). Saving re-indexes watched entities and re-renders.
- Vendors js-yaml at build time for the YAML editor.

## 0.4.0

- Read-only web UI (ingress): lists configured displays with hardware/renderer and the
  entities each one watches, plus a live preview image that auto-refreshes every 5s so
  state-driven re-renders show up. Replaces the placeholder page.
- Ingress-side API: `GET /api/displays` and `GET /api/displays/<name>.png`.

## 0.3.0

- Home Assistant websocket state sync: the add-on authenticates with its Supervisor
  token, loads current states, and subscribes to `state_changed`.
- Displays auto-render when a watched entity changes (entities are extracted from each
  display config); only affected displays re-render, debounced to coalesce bursts.
- Replaces the old rest_command + automation approach — no HA YAML needed. `POST
  /render/<name>` remains for manual/debug use.

## 0.2.0

- Render engine ported in: displays render to PNG + raw `.bin` (packed framebuffer)
  under `/data/output`, with a hash no-op to skip unchanged writes.
- Image endpoint live on port 5123 (no auth): `GET /displays/<name>.png|.bin`,
  `GET /displays`, and `POST /render/<name>`.
- `/data` is seeded with the bundled `tea_menu` template + `waveshare_7in5_v2`
  hardware profile on first run; all displays render on startup.
- LXGW WenKai Screen font (OFL) is fetched at build time.

## 0.1.0

- Initial add-on skeleton: aiohttp app served under Home Assistant ingress with an
  Alpine.js placeholder page and a `/health` endpoint.
