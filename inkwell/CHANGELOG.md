# Changelog

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
