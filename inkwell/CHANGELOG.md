# Changelog

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
