# inkwell

Server-side e-ink display renderer. Home Assistant pushes entity states to trigger a render; an ESP32 wakes from deep sleep, fetches the result, pushes it to the display, and goes back to sleep.

```
Home Assistant → POST /render/<name>  →  inkwell container  →  saves PNG + binary
ESP32 (15 min wake)  →  GET /displays/<name>.bin  →  e-ink display  →  deep sleep
```

## Hardware

- **Display:** Waveshare 7.5" V2 (800×480, black/white)
- **MCU:** ESP32 (esp32dev board)
- **Server:** any machine that can run Docker and is reachable from both HA and the ESP32

---

## Getting started

### 1. Start the renderer

```bash
# Edit docker-compose.yaml and set the port if needed (default 5123)
docker compose up -d
```

Verify it's running:

```bash
curl http://localhost:5123/health
# {"status": "ok"}

curl http://localhost:5123/displays
# {"displays": [{"name": "tea_menu", ...}]}
```

### 2. Trigger a test render

```bash
curl -s -X POST http://localhost:5123/render/tea_menu \
  -H "Content-Type: application/json" \
  -d '{}'
```

Then open `http://localhost:5123/displays/tea_menu.png` in a browser to see the output.

### 3. Configure Home Assistant

Copy `ha_config/` to your HA config directory (or merge into existing files).

In `ha_config/packages/tea_menu.yaml`, replace `RENDERER_ADDR` with your renderer's IP or hostname:

```yaml
rest_command:
  render_tea_menu:
    url: "http://192.168.1.50:5123/render/tea_menu"
```

In `ha_config/configuration.yaml`, add the packages include if it isn't there:

```yaml
homeassistant:
  packages:
    tea_menu: !include packages/tea_menu.yaml
```

Restart Home Assistant. The automation will call the renderer whenever any of the tea menu input entities change.

### 4. Flash the ESP32

In `esphome/display-tea-menu.yaml`, set your renderer address:

```yaml
substitutions:
  renderer_url: "http://192.168.1.50:5123"
```

Create `esphome/secrets.yaml` (not tracked):

```yaml
wifi_ssid: "YourNetwork"
wifi_password: "YourPassword"
```

Then flash:

```bash
esphome run esphome/display-tea-menu.yaml
```

After the first wired flash, OTA updates work during the 60-second window after each display refresh.

---

## Project structure

```
eink-renderer/
  hardware/          hardware profiles (dimensions, color mode)
  displays/          display configs (which hardware + renderer + layout)
  renderers/         Python rendering logic
  fonts/             fonts downloaded at build time (not tracked)
  server.py          Flask HTTP server

ha_config/           Home Assistant config (packages + configuration.yaml)
esphome/             ESPHome firmware for the ESP32
```

---

## Adding a new menu item

Open `eink-renderer/displays/tea_menu.yaml` and add to the relevant section.

**Static item** — always shown:

```yaml
- type: static
  header: "現泡"
  items:
    - { text: "新品茶" }        # add here
    - { text: "伯爵紅茶" }
```

**Conditional item** — shown only when an entity is on:

```yaml
- type: conditional
  header: "拿鐵"
  condition:
    require_all: ["input_boolean.tea_milk"]
    require_any_item_active: true
  items:
    - text: "新拿鐵"
      show_when: { entity_on: "input_boolean.tea_new_item" }
```

You'll also need to add the corresponding `input_boolean` in `ha_config/packages/tea_menu.yaml` and add it to the automation trigger list and the `rest_command` payload.

**Bottle option** — add a value to the shared options list:

```yaml
input_select:
  tea_red_bottle:
    options: &bottle_options
      - none
      - "Earl Grey"
      - "新茶款"           # add here
```

The `*bottle_options` anchor on the other two bottles picks it up automatically.

---

## Adding a new display

### 1. Create a display config

`eink-renderer/displays/my_display.yaml`:

```yaml
hardware: waveshare_7in5_v2
renderer: menu        # or dashboard, or a custom renderer

fonts:
  title:  { file: "LXGWWenKaiScreen.ttf", size: 72 }
  header: { file: "LXGWWenKaiScreen.ttf", size: 45 }
  body:   { file: "LXGWWenKaiScreen.ttf", size: 27 }

sections:
  - type: static
    header: "My Section"
    items:
      - { text: "Item one" }
      - { text: "Item two" }
```

The server picks it up automatically on the next request — no restart needed.

### 2. Trigger a render

```bash
curl -X POST http://localhost:5123/render/my_display \
  -H "Content-Type: application/json" \
  -d '{"sensor.my_entity": "on"}'
```

### 3. Add a Home Assistant automation

Follow the pattern in `ha_config/packages/tea_menu.yaml`: create `input_boolean`/`input_select` entities, a `rest_command`, and an automation that sends their states on change.

---

## Adding a new hardware profile

`eink-renderer/hardware/my_display.yaml`:

```yaml
name: "My Display"
width: 800
height: 480
color_mode: "1"    # "1" = 1-bit B&W, "L" = 8-bit grayscale, "RGB" = color
bg_color: 255      # white background (use list [255,255,255] for RGB)
fg_color: 0        # black foreground
```

Reference it in your display config with `hardware: my_display`.

---

## Writing a custom renderer

Create `eink-renderer/renderers/my_renderer.py`:

```python
def render(image, draw, config, hardware, states, fonts):
    w, h = hardware["width"], hardware["height"]
    fg = hardware["fg_color"]

    font = fonts["body"]
    draw.text((20, 20), "Hello world", font=font, fill=fg)

    # states is {"entity_id": "state_string", ...}
    temp = states.get("sensor.living_room_temp", "?")
    draw.text((20, 80), f"Temp: {temp}°C", font=font, fill=fg)
```

Then reference it in a display config with `renderer: my_renderer`. The server loads renderers at request time — no restart needed.

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/render/<name>` | Render display. Body: `{"entity_id": "state", ...}` |
| `GET`  | `/displays/<name>.png` | Rendered PNG (for preview) |
| `GET`  | `/displays/<name>.bin` | Raw 1-bit binary for ESP32 (48 000 bytes for 800×480) |
| `GET`  | `/displays` | List all configured displays |
| `GET`  | `/health` | Health check |

Render is a no-op if the image hasn't changed (hash comparison).

---

## OTA updates (ESP32)

The ESP32 enters deep sleep after displaying, so it's not normally reachable for OTA. To update firmware:

- **Normal window:** 60 seconds after each successful display refresh
- **Extended window:** power-cycle the ESP32 three times rapidly to trigger ESPHome safe mode, which keeps it awake indefinitely
