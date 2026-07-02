"""Inkwell render engine.

Ported from the original standalone `server.py` render_display(), adapted for the
add-on layout:
  - user-editable configs live in /data (displays, hardware, fonts, output)
  - code-baked assets live next to this module (renderers, default fonts, defaults)

Produces, per display: a PNG (preview / ESPHome online_image) and a raw .bin
(packed framebuffer for direct e-ink push), skipping the write when the rendered
image is byte-identical to the last one (hash no-op).
"""

import hashlib
import importlib.util
import logging
import os
import shutil
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

APP_DIR = Path(__file__).parent                    # /app/backend
# User-editable config lives in the mapped addon_config dir (/config), which HAOS
# exposes as the addon_configs/<slug> share — so users (and the web UI) can drop
# fonts and edit configs there. Rendered artefacts stay in the private /data.
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

DISPLAYS_DIR = CONFIG_DIR / "displays"             # user-editable
HARDWARE_DIR = CONFIG_DIR / "hardware"             # user-editable
USER_FONTS_DIR = CONFIG_DIR / "fonts"              # user-dropped / UI-uploaded fonts
OUTPUT_DIR = DATA_DIR / "output"                   # rendered artefacts

RENDERERS_DIR = APP_DIR / "renderers"              # baked into image
DEFAULTS_DIR = APP_DIR / "defaults"                # seed templates + profiles
BAKED_FONTS_DIR = DEFAULTS_DIR / "fonts"           # bundled OFL fonts

log = logging.getLogger("inkwell.render")


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_display_config(name: str) -> dict:
    path = DISPLAYS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Display config not found: {path}")
    return load_yaml(path)


def load_hardware(name: str) -> dict:
    path = HARDWARE_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Hardware profile not found: {path}")
    return load_yaml(path)


def list_displays() -> list[str]:
    if not DISPLAYS_DIR.exists():
        return []
    return sorted(
        p.stem for p in DISPLAYS_DIR.glob("*.yaml") if not p.name.endswith(".example")
    )


def load_renderer(name: str):
    path = RENDERERS_DIR / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"Renderer not found: {path}")
    spec = importlib.util.spec_from_file_location(f"renderers.{name}", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "render"):
        raise AttributeError(f"Renderer '{name}' has no render() function")
    return module


def _resolve_font(filename: str) -> Path:
    """User-uploaded fonts (/data/fonts) take precedence over bundled ones."""
    for base in (USER_FONTS_DIR, BAKED_FONTS_DIR):
        candidate = base / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Font file '{filename}' not found in {USER_FONTS_DIR} or {BAKED_FONTS_DIR}"
    )


def load_fonts(config: dict) -> dict:
    fonts = {}
    for name, fdef in config.get("fonts", {}).items():
        fpath = _resolve_font(fdef["file"])
        size = fdef["size"]
        try:
            fonts[name] = ImageFont.truetype(str(fpath), size)
        except Exception as e:  # fail loud — no silent default-font fallback
            raise RuntimeError(f"Font '{name}' failed to load ({fpath} @ {size}px): {e}") from e
    return fonts


def render_display(display_name: str, states: dict) -> tuple[bool, str]:
    """Render one display to PNG + BIN. Returns (changed, png_path)."""
    config = load_display_config(display_name)
    hw = load_hardware(config.get("hardware", "waveshare_7in5_v2"))
    renderer = load_renderer(config.get("renderer", "menu"))
    fonts = load_fonts(config)

    color_mode = hw.get("color_mode", "L")
    bg = hw.get("bg_color", 0)
    image = Image.new(color_mode, (hw["width"], hw["height"]), bg)
    draw = ImageDraw.Draw(image)

    renderer.render(
        image=image, draw=draw, config=config, hardware=hw, states=states, fonts=fonts
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUTPUT_DIR / f"{display_name}.png"
    bin_path = OUTPUT_DIR / f"{display_name}.bin"
    hash_path = OUTPUT_DIR / f"{display_name}.hash"

    raw = image.tobytes()
    new_hash = hashlib.md5(raw).hexdigest()
    old_hash = hash_path.read_text().strip() if hash_path.exists() else ""
    if new_hash == old_hash and png_path.exists() and bin_path.exists():
        log.info("[%s] No change.", display_name)
        return False, str(png_path)

    image.save(str(png_path), "PNG")
    bin_path.write_bytes(raw)  # packed framebuffer (mode "1" → 1 bit/px)
    hash_path.write_text(new_hash)
    log.info(
        "[%s] Rendered -> %s (%d B png, %d B bin)",
        display_name, png_path, png_path.stat().st_size, len(raw),
    )
    return True, str(png_path)


def seed_defaults() -> None:
    """Populate /data with bundled templates/profiles on first run (idempotent)."""
    DISPLAYS_DIR.mkdir(parents=True, exist_ok=True)
    HARDWARE_DIR.mkdir(parents=True, exist_ok=True)
    USER_FONTS_DIR.mkdir(parents=True, exist_ok=True)

    if not any(DISPLAYS_DIR.glob("*.yaml")):
        for src in (DEFAULTS_DIR / "templates").glob("*.yaml"):
            shutil.copy(src, DISPLAYS_DIR / src.name)
            log.info("Seeded display: %s", src.name)
    if not any(HARDWARE_DIR.glob("*.yaml")):
        for src in (DEFAULTS_DIR / "hardware").glob("*.yaml"):
            shutil.copy(src, HARDWARE_DIR / src.name)
            log.info("Seeded hardware profile: %s", src.name)


def render_all(states: dict | None = None) -> None:
    states = states or {}
    for name in list_displays():
        try:
            render_display(name, states)
        except Exception as e:  # one bad display shouldn't stop the rest
            log.warning("Render %s failed: %s", name, e)
