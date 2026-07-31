# Inkwell

Server-side e-ink display renderer with a web UI, running as a Home Assistant add-on.

> **Status:** menu builder — a structured editor for the new menu schema (option sets,
> groups, items with composable conditions), inkwell-managed HA helpers with one-click
> creation, legacy-format migration, and an Advanced (YAML) tab. Onboarding wizard, device
> fleet view, and ESPHome generation come next. See `.notes/addon-design.md`.

## Home Assistant state sync

The add-on subscribes to Home Assistant and re-renders a display whenever one of the
entities referenced in its config changes — no automations or `rest_command` needed.
Just reference the entities in a display config and they're watched automatically.

## Image endpoint (port 5123, no auth)

| Method | Path | Description |
|---|---|---|
| `GET` | `/displays/<name>.png` | Rendered PNG (preview / ESPHome `online_image`) |
| `GET` | `/displays/<name>.bin` | Raw packed framebuffer (48 000 B for an 800×480 1-bit display) |
| `GET` | `/displays` | List configured displays |
| `POST` | `/render/<name>` | Force a render. Body: `{"entity_id": "state", ...}` |

## Installation

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories** → add
   `https://github.com/rootn/inkwell`.
2. Install **Inkwell**, then **Start** it.
3. Open the **Inkwell** panel from the sidebar — you should see the running page.

## Options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Log verbosity: `debug`, `info`, `warning`, `error`. |

## Fonts

Display configs reference fonts by filename. Fonts live in the add-on's config share,
under the `fonts/` subfolder:

- On HAOS/Supervised, browse to the **`addon_configs/<slug>/fonts/`** folder (e.g.
  `addon_configs/local_inkwell/fonts/`) via the Samba or SSH add-on and drop `.ttf`
  files in there.
- A later release adds font upload directly from the web UI, writing to the same folder.

The default **LXGW WenKai Screen** font (`LXGWWenKaiScreen.ttf`) is bundled, so the sample
`tea_menu` renders out of the box; fonts you upload to the config folder take precedence.
If a display references a font that isn't present, it logs a warning and its image endpoint
returns `404` until you provide that font.

### Font licensing

The bundled **LXGW WenKai Screen** is © LXGW and The Klee Project Authors, under the
[SIL Open Font License 1.1](https://scripts.sil.org/OFL) — its `OFL.txt` ships alongside the
font in the image. Fonts you upload remain under their own licenses.

## Ports

| Port | Purpose |
|---|---|
| `5123/tcp` | E-ink image endpoint for ESP32 screens (no auth). |
