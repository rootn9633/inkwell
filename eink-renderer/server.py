#!/usr/bin/env python3
"""
E-Ink Renderer Service
======================
HTTP server that renders e-ink display images and serves them.

Endpoints:
  POST /render/<display_name>       Trigger render. Body: {entity states}
  GET  /displays/<name>.png         Serve rendered image
  GET  /health                      Health check
  GET  /displays/<name>.bin         Serve raw 1-bit packed binary (48000 bytes for 800x480)
  GET  /displays                    List displays

CLI:
  python3 server.py render <name> --states '{"entity":"value",...}'
  python3 server.py render --list
"""

import sys
import os
import json
import yaml
import hashlib
import logging
import importlib.util
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("ERROR: Pillow required. pip install Pillow")
    sys.exit(1)

try:
    from flask import Flask, request, send_file, make_response
except ImportError:
    print("ERROR: Flask required. pip install flask")
    sys.exit(1)

BASE_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
APP_DIR = Path(__file__).parent          # /app — renderers/ and fonts/ are baked in here
DISPLAYS_DIR = BASE_DIR / "displays"
HARDWARE_DIR = BASE_DIR / "hardware"
RENDERERS_DIR = APP_DIR / "renderers"   # /app/renderers, not /config/renderers
FONTS_DIR = APP_DIR / "fonts"           # /app/fonts, baked into image
OUTPUT_DIR = BASE_DIR / "output"
PORT = int(os.environ.get("PORT", "5123"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("eink")

app = Flask(__name__)


# --- helpers ---

def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_display_config(name):
    path = DISPLAYS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Display config not found: {path}")
    return load_yaml(path)

def load_hardware(name):
    path = HARDWARE_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Hardware profile not found: {path}")
    return load_yaml(path)

def list_displays():
    if not DISPLAYS_DIR.exists():
        return []
    return sorted(p.stem for p in DISPLAYS_DIR.glob("*.yaml")
                  if not p.name.endswith(".example"))

def load_renderer(name):
    path = RENDERERS_DIR / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"Renderer not found: {path}")
    spec = importlib.util.spec_from_file_location(f"renderers.{name}", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "render"):
        raise AttributeError(f"Renderer '{name}' has no render() function")
    return module

def load_fonts(config):
    fonts = {}
    for name, fdef in config.get("fonts", {}).items():
        fpath = FONTS_DIR / fdef["file"]
        size = fdef["size"]
        try:
            fonts[name] = ImageFont.truetype(str(fpath), size)
        except Exception as e:
            raise RuntimeError(
                f"Font '{name}' failed to load ({fpath} @ {size}px): {e}"
            ) from e
    return fonts

def render_display(display_name, states):
    config = load_display_config(display_name)
    hw = load_hardware(config.get("hardware", "waveshare_7in5_v2"))
    renderer = load_renderer(config.get("renderer", "menu"))
    fonts = load_fonts(config)

    color_mode = hw.get("color_mode", "L")
    bg = hw.get("bg_color", 0)
    image = Image.new(color_mode, (hw["width"], hw["height"]), bg)
    draw = ImageDraw.Draw(image)

    renderer.render(image=image, draw=draw, config=config,
                    hardware=hw, states=states, fonts=fonts)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{display_name}.png"
    hash_path = OUTPUT_DIR / f"{display_name}.hash"

    new_hash = hashlib.md5(image.tobytes()).hexdigest()
    old_hash = hash_path.read_text().strip() if hash_path.exists() else ""

    if new_hash == old_hash:
        log.info("[%s] No change.", display_name)
        return False, str(out_path)

    image.save(str(out_path), "PNG")
    hash_path.write_text(new_hash)
    log.info("[%s] Rendered → %s (%d bytes)", display_name, out_path, out_path.stat().st_size)
    return True, str(out_path)


# --- routes ---

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/displays")
def get_displays():
    displays = []
    for name in list_displays():
        cfg = load_display_config(name)
        displays.append({
            "name": name,
            "hardware": cfg.get("hardware", "?"),
            "renderer": cfg.get("renderer", "?"),
            "has_image": (OUTPUT_DIR / f"{name}.png").exists(),
        })
    return {"displays": displays}

@app.get("/displays/<name>.png")
def serve_image(name):
    img = OUTPUT_DIR / f"{name}.png"
    if not img.exists():
        return {"error": f"No image for '{name}'"}, 404
    response = send_file(img, mimetype="image/png")
    response.headers["Cache-Control"] = "no-cache"
    return response

@app.get("/displays/<name>.bin")
def serve_binary(name):
    img_path = OUTPUT_DIR / f"{name}.png"
    if not img_path.exists():
        return {"error": f"No image for '{name}'"}, 404
    img = Image.open(str(img_path))
    if img.mode != "1":
        img = img.convert("1")
    response = make_response(img.tobytes())
    response.headers["Content-Type"] = "application/octet-stream"
    response.headers["Cache-Control"] = "no-cache"
    return response

@app.post("/render/<display_name>")
def render(display_name):
    states = request.get_json(silent=True) or {}
    changed, _ = render_display(display_name, states)
    return {"display": display_name, "changed": changed,
            "image_url": f"/displays/{display_name}.png"}


# --- error handlers ---

@app.errorhandler(FileNotFoundError)
def handle_not_found(e):
    return {"error": str(e)}, 404

@app.errorhandler(Exception)
def handle_error(e):
    log.exception("Unhandled error")
    return {"error": str(e)}, 500


# --- server + CLI ---

def run_server():
    log.info("E-Ink Renderer on port %d", PORT)
    for name in list_displays():
        try: render_display(name, {})
        except Exception as e: log.warning("Startup render %s: %s", name, e)
    app.run(host="0.0.0.0", port=PORT)


def cli():
    if len(sys.argv) < 2 or sys.argv[1] != "render":
        run_server(); return
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("_cmd"); p.add_argument("display", nargs="?")
    p.add_argument("--list", action="store_true")
    p.add_argument("--states", type=str, default=None)
    a = p.parse_args()
    if a.list:
        for n in list_displays():
            c = load_display_config(n)
            print(f"  {n:20s}  hw={c.get('hardware','?'):20s}  renderer={c.get('renderer','?')}")
        return
    if not a.display: p.print_help(); return
    states = json.loads(a.states) if a.states else {}
    changed, path = render_display(a.display, states)
    print(f"{'Changed' if changed else 'Unchanged'}: {path}")


if __name__ == "__main__":
    cli()
