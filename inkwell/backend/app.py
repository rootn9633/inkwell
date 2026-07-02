#!/usr/bin/env python3
"""Inkwell add-on — aiohttp application.

Runs two HTTP servers in one process:
  - ingress (INGRESS_PORT): the web UI, reachable only via authenticated HA ingress
  - image  (IMAGE_PORT):    the raw, no-auth e-ink endpoint the ESP32 fetches from

Phase 2: the image server renders and serves /displays/<name>.png|.bin from /data.
The HA websocket state sync (auto re-render) arrives in Phase 3; for now displays
are rendered on startup and can be re-rendered via POST /render/<name>.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

import jinja2
from aiohttp import web

import ha_ws
import render

APP_DIR = Path(__file__).parent
FRONTEND_DIR = APP_DIR.parent / "frontend"
TEMPLATES_DIR = FRONTEND_DIR / "templates"
STATIC_DIR = FRONTEND_DIR / "static"

INGRESS_PORT = int(os.environ.get("INGRESS_PORT", "8099"))
IMAGE_PORT = int(os.environ.get("IMAGE_PORT", "5123"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("inkwell")

_jinja = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
)


# ---------------------------------------------------------------- ingress UI

def ingress_base(request: web.Request) -> str:
    return request.headers.get("X-Ingress-Path", "")


async def index(request: web.Request) -> web.Response:
    html = _jinja.get_template("index.html").render(base=ingress_base(request))
    return web.Response(text=html, content_type="text/html")


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def api_displays(request: web.Request) -> web.Response:
    """Read-only display list for the UI (served under ingress auth)."""
    items = []
    for name in render.list_displays():
        try:
            cfg = render.load_display_config(name)
        except Exception as e:
            log.warning("List %s failed: %s", name, e)
            continue
        png = render.OUTPUT_DIR / f"{name}.png"
        items.append({
            "name": name,
            "hardware": cfg.get("hardware", "?"),
            "renderer": cfg.get("renderer", "?"),
            "entities": sorted(ha_ws.extract_entities(cfg)),
            "has_image": png.exists(),
            "modified": png.stat().st_mtime if png.exists() else None,
        })
    return web.json_response({"displays": items})


def create_ingress_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/displays", api_displays)
    app.router.add_get("/api/displays/{name}.png", display_png)  # preview, under ingress
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")
    return app


# ------------------------------------------------------------- image server

def _serve_artifact(name: str, suffix: str, content_type: str) -> web.StreamResponse:
    path = render.OUTPUT_DIR / f"{name}.{suffix}"
    if not path.exists():
        return web.json_response({"error": f"No {suffix} for '{name}'"}, status=404)
    return web.FileResponse(
        path, headers={"Cache-Control": "no-cache", "Content-Type": content_type}
    )


async def display_png(request: web.Request) -> web.StreamResponse:
    return _serve_artifact(request.match_info["name"], "png", "image/png")


async def display_bin(request: web.Request) -> web.StreamResponse:
    return _serve_artifact(request.match_info["name"], "bin", "application/octet-stream")


async def list_displays(request: web.Request) -> web.Response:
    displays = []
    for name in render.list_displays():
        cfg = render.load_display_config(name)
        displays.append({
            "name": name,
            "hardware": cfg.get("hardware", "?"),
            "renderer": cfg.get("renderer", "?"),
            "has_image": (render.OUTPUT_DIR / f"{name}.png").exists(),
        })
    return web.json_response({"displays": displays})


async def render_endpoint(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    body = await request.text()
    try:
        states = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)
    try:
        changed, _ = render.render_display(name, states)
    except FileNotFoundError as e:
        return web.json_response({"error": str(e)}, status=404)
    except Exception as e:
        log.exception("Render failed")
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response(
        {"display": name, "changed": changed, "image_url": f"/displays/{name}.png"}
    )


def create_image_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/displays", list_displays)
    app.router.add_get("/displays/{name}.png", display_png)
    app.router.add_get("/displays/{name}.bin", display_bin)
    app.router.add_post("/render/{name}", render_endpoint)
    return app


# ---------------------------------------------------------------- state sync

ENTITY_TO_DISPLAYS: dict[str, set[str]] = {}
STATES: dict[str, str] = {}
_pending: set[str] = set()
_debounce_task: "asyncio.Task | None" = None
DEBOUNCE_SECONDS = 0.4


def build_index() -> set[str]:
    """Map each watched entity to the displays using it; return the watched set."""
    ENTITY_TO_DISPLAYS.clear()
    for name in render.list_displays():
        try:
            cfg = render.load_display_config(name)
        except Exception as e:
            log.warning("Index %s failed: %s", name, e)
            continue
        for entity in ha_ws.extract_entities(cfg):
            ENTITY_TO_DISPLAYS.setdefault(entity, set()).add(name)
    return set(ENTITY_TO_DISPLAYS)


async def _render_pending() -> None:
    await asyncio.sleep(DEBOUNCE_SECONDS)  # coalesce bursts of state changes
    names = set(_pending)
    _pending.clear()
    for name in names:
        try:
            render.render_display(name, STATES)
        except Exception as e:
            log.warning("Render %s failed: %s", name, e)


def on_states(states: dict, changed: set) -> None:
    """Sync callback: re-render only the displays affected by changed entities."""
    global _debounce_task
    STATES.update(states)
    affected: set[str] = set()
    for entity in changed:
        affected |= ENTITY_TO_DISPLAYS.get(entity, set())
    if not affected:
        return
    _pending.update(affected)
    if _debounce_task and not _debounce_task.done():
        _debounce_task.cancel()
    _debounce_task = asyncio.create_task(_render_pending())


# -------------------------------------------------------------------- boot

async def _run() -> None:
    render.seed_defaults()
    watched = build_index()
    render.render_all()  # baseline (empty states) until HA states arrive

    runners = []
    for application, port, label in (
        (create_ingress_app(), INGRESS_PORT, "ingress UI"),
        (create_image_app(), IMAGE_PORT, "image endpoint"),
    ):
        runner = web.AppRunner(application)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()
        runners.append(runner)
        log.info("Serving %s on port %d", label, port)

    sync = ha_ws.HAStateSync(watched, on_states)
    sync_task = asyncio.create_task(sync.run())
    log.info("HA state sync watching %d entities", len(watched))

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        sync_task.cancel()
        for runner in runners:
            await runner.cleanup()


def main() -> None:
    log.info("Inkwell starting")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
