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
import io
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

NAME_RE = re.compile(r"^[a-z0-9_-]+$")

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


def render_to_png_bytes(config: dict, states: dict) -> bytes:
    """Render a config dict straight to PNG bytes without persisting — used for the
    editor's live preview of unsaved edits. Raises on invalid config (missing font, etc.)."""
    hw = load_hardware(config.get("hardware", "waveshare_7in5_v2"))
    renderer = load_renderer(config.get("renderer", "menu"))
    fonts = load_fonts(config)

    image = Image.new(hw.get("color_mode", "L"), (hw["width"], hw["height"]), hw.get("bg_color", 0))
    draw = ImageDraw.Draw(image)
    renderer.render(image=image, draw=draw, config=config, hardware=hw, states=states, fonts=fonts)

    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


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


# ------------------------------------------------------------------ editing

def valid_name(name: str) -> bool:
    return bool(name) and bool(NAME_RE.match(name))


def display_path(name: str) -> Path:
    return DISPLAYS_DIR / f"{name}.yaml"


def save_display_config(name: str, cfg: dict) -> None:
    """Atomically write a display config to /config/displays/<name>.yaml."""
    DISPLAYS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DISPLAYS_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, display_path(name))
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _remove_outputs(name: str) -> None:
    for suffix in (".png", ".bin", ".hash"):
        p = OUTPUT_DIR / f"{name}{suffix}"
        if p.exists():
            p.unlink()


def delete_display(name: str) -> None:
    if display_path(name).exists():
        display_path(name).unlink()
    _remove_outputs(name)


def rename_display(old: str, new: str) -> None:
    os.replace(display_path(old), display_path(new))
    for suffix in (".png", ".bin", ".hash"):
        src = OUTPUT_DIR / f"{old}{suffix}"
        if src.exists():
            src.replace(OUTPUT_DIR / f"{new}{suffix}")


def list_hardware() -> list[str]:
    if not HARDWARE_DIR.exists():
        return []
    return sorted(p.stem for p in HARDWARE_DIR.glob("*.yaml"))


def list_renderers() -> list[str]:
    return sorted(p.stem for p in RENDERERS_DIR.glob("*.py") if not p.stem.startswith("_"))


def list_fonts() -> list[str]:
    fonts: set[str] = set()
    for base in (USER_FONTS_DIR, BAKED_FONTS_DIR):
        if base.exists():
            for ext in ("*.ttf", "*.otf"):
                fonts.update(p.name for p in base.glob(ext))
    return sorted(fonts)


_BLANK_MENU = {
    "hardware": "waveshare_7in5_v2",
    "renderer": "menu",
    "fonts": {
        "title": {"file": "LXGWWenKaiScreen.ttf", "size": 72},
        "header": {"file": "LXGWWenKaiScreen.ttf", "size": 45},
        "body": {"file": "LXGWWenKaiScreen.ttf", "size": 27},
    },
    "title": {"text": "", "font": "title", "start_y": 60, "char_spacing": 100, "separator_x": 110},
    "sections": [],
}

_BLANK_DASHBOARD = {
    "hardware": "waveshare_7in5_v2",
    "renderer": "dashboard",
    "fonts": {
        "title": {"file": "LXGWWenKaiScreen.ttf", "size": 40},
        "label": {"file": "LXGWWenKaiScreen.ttf", "size": 20},
        "value": {"file": "LXGWWenKaiScreen.ttf", "size": 36},
    },
    "dashboard_title": "",
    "grid": {"columns": 3, "row_height": 130, "padding": 12},
    "cards": [],
}


def new_config(template: str | None) -> dict:
    """Starter config: duplicate an existing display, or a blank menu/dashboard."""
    import copy
    if template and display_path(template).exists():
        return load_display_config(template)
    if template == "blank_dashboard":
        return copy.deepcopy(_BLANK_DASHBOARD)
    return copy.deepcopy(_BLANK_MENU)
