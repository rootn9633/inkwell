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
import ledger
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


async def api_get_display(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not render.valid_name(name):
        return web.json_response({"error": "Invalid name"}, status=400)
    try:
        cfg = render.load_display_config(name)
    except FileNotFoundError:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response({"name": name, "config": cfg})


async def api_create_display(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    name = (body.get("name") or "").strip()
    if not render.valid_name(name):
        return web.json_response({"error": "Invalid name (use a-z, 0-9, _, -)"}, status=400)
    if render.display_path(name).exists():
        return web.json_response({"error": f"'{name}' already exists"}, status=409)
    cfg = render.new_config(body.get("template"))
    render.save_display_config(name, cfg)
    await _reindex_and_prime([name])
    return web.json_response({"name": name, "config": cfg})


async def api_save_display(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not render.valid_name(name):
        return web.json_response({"error": "Invalid name"}, status=400)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    cfg = body.get("config")
    if not isinstance(cfg, dict):
        return web.json_response({"error": "config must be an object"}, status=400)
    render.save_display_config(name, cfg)
    await _reindex_and_prime()
    result = {"saved": True, "rendered": True}
    try:
        render.render_display(name, STATES)
    except Exception as e:
        result["rendered"] = False
        result["error"] = str(e)
    return web.json_response(result)


async def api_delete_display(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not render.valid_name(name):
        return web.json_response({"error": "Invalid name"}, status=400)
    render.delete_display(name)
    await _reindex_and_prime()
    return web.json_response({"deleted": name})


async def api_rename_display(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not render.valid_name(name):
        return web.json_response({"error": "Invalid name"}, status=400)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    new = (body.get("new_name") or "").strip()
    if not render.valid_name(new):
        return web.json_response({"error": "Invalid new name"}, status=400)
    if not render.display_path(name).exists():
        return web.json_response({"error": "Not found"}, status=404)
    if render.display_path(new).exists():
        return web.json_response({"error": f"'{new}' already exists"}, status=409)
    render.rename_display(name, new)
    await _reindex_and_prime([new])
    return web.json_response({"name": new})


async def api_preview(request: web.Request) -> web.Response:
    """Render an unsaved config and cache it (keyed by display name) for the editor's
    live preview. Served back via GET /api/preview/<name>.png — a normal same-origin URL,
    which ingress allows (blob:/data: URLs are blocked by the ingress CSP)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    name = (body.get("name") or "").strip()
    if not render.valid_name(name):
        name = "_preview"
    cfg = body.get("config")
    if not isinstance(cfg, dict):
        return web.json_response({"error": "config must be an object"}, status=400)
    try:
        PREVIEWS[name] = render.render_to_png_bytes(cfg, STATES)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True, "name": name})


async def api_preview_png(request: web.Request) -> web.StreamResponse:
    data = PREVIEWS.get(request.match_info["name"])
    if data is None:
        return web.json_response({"error": "No preview"}, status=404)
    return web.Response(body=data, content_type="image/png", headers={"Cache-Control": "no-cache"})


HELPER_DOMAINS = {"input_boolean", "input_select"}  # domains inkwell can auto-create


def _resolve_options(config: dict, entity_id: str) -> list:
    """Options for a managed input_select — from the Choice item that uses it."""
    for g in config.get("groups", []):
        for item in g.get("items", []):
            if item.get("control") == "choice" and item.get("entity") == entity_id:
                opts = item.get("options")
                if isinstance(opts, str):
                    return list(config.get("option_sets", {}).get(opts, []))
                if isinstance(opts, list):
                    return list(opts)
    return []


def _config_helper_targets(config: dict) -> dict:
    """Referenced input_boolean/input_select entities inkwell can create (+ select options)."""
    targets = {}
    for eid in ha_ws.extract_entities(config):
        domain = eid.split(".", 1)[0]
        if domain not in HELPER_DOMAINS:
            continue
        info = {"entity_id": eid, "domain": domain}
        if domain == "input_select":
            info["options"] = _resolve_options(config, eid)
        targets[eid] = info
    return targets


def _all_referenced_targets() -> set:
    """Helper entity_ids referenced by any display (for orphan detection)."""
    refs = set()
    for name in render.list_displays():
        try:
            refs |= set(_config_helper_targets(render.load_display_config(name)))
        except Exception:
            continue
    return refs


async def api_helpers_status(request: web.Request) -> web.Response:
    """Per-helper status for a display: exists / owned / storage / drifted (selects)."""
    name = request.match_info["name"]
    try:
        config = render.load_display_config(name)
    except FileNotFoundError:
        return web.json_response({"error": "Not found"}, status=404)
    targets = _config_helper_targets(config)
    try:
        by_id = {e["entity_id"]: e for e in await ha_ws.fetch_entities()}
    except Exception as e:
        return web.json_response({"error": str(e), "helpers": []}, status=502)
    led = ledger.load()
    storage = {}
    for domain in {t["domain"] for t in targets.values()}:
        try:
            storage |= await ha_ws.list_storage_helpers(domain)
        except Exception:
            pass
    helpers = []
    for eid, t in sorted(targets.items()):
        exists = eid in by_id
        s = {"entity_id": eid, "domain": t["domain"], "exists": exists,
             "owned": eid in led, "storage": eid in storage}
        if t["domain"] == "input_select":
            desired = t.get("options") or []
            s["desired_options"] = desired
            if not desired:
                s["needs_options"] = True
            if exists and s["owned"] and desired:
                cur = by_id[eid].get("options") or []
                if cur != desired:
                    s["drifted"] = True
                    val = by_id[eid].get("state")
                    if val not in desired and val not in (None, "", "unknown", "unavailable"):
                        s["value_drop"] = val
        helpers.append(s)
    return web.json_response({"helpers": helpers})


async def api_create_helpers(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    try:
        config = render.load_display_config(name)
    except FileNotFoundError:
        return web.json_response({"error": "Not found"}, status=404)
    targets = _config_helper_targets(config)
    try:
        existing = {e["entity_id"] for e in await ha_ws.fetch_entities()}
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)
    created, errors = [], []
    for eid, info in sorted(targets.items()):
        if eid in existing:
            continue
        try:
            if info["domain"] == "input_select" and not info.get("options"):
                raise ValueError("no options — add options to the Choice that uses this select")
            await ha_ws.create_helper(eid, info.get("options"))
            ledger.add(eid, info["domain"])   # created => inkwell owns it
            created.append(eid)
        except Exception as e:
            errors.append({"entity_id": eid, "error": str(e)})
    if created:
        await _reindex_and_prime([name])
        log.info("Created %d helper(s) for %s: %s", len(created), name, created)
    return web.json_response({"created": created, "errors": errors})


async def api_sync_helpers(request: web.Request) -> web.Response:
    """Push owned input_select options to match the display's option sets (value preserved)."""
    name = request.match_info["name"]
    try:
        config = render.load_display_config(name)
    except FileNotFoundError:
        return web.json_response({"error": "Not found"}, status=404)
    targets = _config_helper_targets(config)
    led = ledger.load()
    try:
        by_id = {e["entity_id"]: e for e in await ha_ws.fetch_entities()}
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)
    synced, warnings, errors = [], [], []
    for eid, t in sorted(targets.items()):
        if t["domain"] != "input_select" or eid not in led or eid not in by_id:
            continue
        desired = t.get("options") or []
        if not desired or (by_id[eid].get("options") or []) == desired:
            continue
        try:
            await ha_ws.update_helper(eid, options=desired)
            synced.append(eid)
            val = by_id[eid].get("state")
            if val not in desired and val not in (None, "", "unknown", "unavailable"):
                warnings.append({"entity_id": eid, "dropped_value": val})
        except Exception as e:
            errors.append({"entity_id": eid, "error": str(e)})
    if synced:
        await _reindex_and_prime([name])
    return web.json_response({"synced": synced, "warnings": warnings, "errors": errors})


async def api_adopt_helpers(request: web.Request) -> web.Response:
    """Adopt referenced *storage* helpers (not YAML) into inkwell's ledger."""
    name = request.match_info["name"]
    try:
        config = render.load_display_config(name)
    except FileNotFoundError:
        return web.json_response({"error": "Not found"}, status=404)
    targets = _config_helper_targets(config)
    led = ledger.load()
    storage = {}
    for domain in {t["domain"] for t in targets.values()}:
        try:
            storage |= await ha_ws.list_storage_helpers(domain)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=502)
    adopted, skipped = [], []
    for eid, t in sorted(targets.items()):
        if eid in led:
            continue
        if eid in storage:
            ledger.add(eid, t["domain"], adopted=True)
            adopted.append(eid)
        else:
            skipped.append(eid)  # YAML/non-storage — inkwell can't manage it
    return web.json_response({"adopted": adopted, "skipped": skipped})


async def api_unused_helpers(request: web.Request) -> web.Response:
    referenced = _all_referenced_targets()
    unused = [eid for eid in sorted(ledger.load()) if eid not in referenced]
    return web.json_response({"unused": unused})


async def api_cleanup_helpers(request: web.Request) -> web.Response:
    referenced = _all_referenced_targets()
    deleted, errors = [], []
    for eid in sorted(ledger.load()):
        if eid in referenced:
            continue
        try:
            await ha_ws.delete_helper(eid)
            ledger.remove(eid)
            deleted.append(eid)
        except Exception as e:
            errors.append({"entity_id": eid, "error": str(e)})
    if deleted:
        log.info("Cleaned up %d unused helper(s): %s", len(deleted), deleted)
    return web.json_response({"deleted": deleted, "errors": errors})


async def api_render_now(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    try:
        changed, _ = render.render_display(name, STATES)
        return web.json_response({"changed": changed})
    except FileNotFoundError as e:
        return web.json_response({"error": str(e)}, status=404)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_entities(request: web.Request) -> web.Response:
    try:
        return web.json_response({"entities": await ha_ws.fetch_entities()})
    except Exception as e:
        return web.json_response({"error": str(e), "entities": []}, status=502)


async def api_hardware(request: web.Request) -> web.Response:
    return web.json_response({"hardware": render.list_hardware()})


async def api_renderers(request: web.Request) -> web.Response:
    return web.json_response({"renderers": render.list_renderers()})


async def api_fonts(request: web.Request) -> web.Response:
    return web.json_response({"fonts": render.list_fonts()})


MAX_FONT_BYTES = 30 * 1024 * 1024


async def api_upload_font(request: web.Request) -> web.Response:
    reader = await request.multipart()
    field = await reader.next()
    while field is not None and field.name != "file":
        field = await reader.next()
    if field is None:
        return web.json_response({"error": "No file field"}, status=400)
    filename = os.path.basename(field.filename or "")
    if not filename.lower().endswith((".ttf", ".otf")):
        return web.json_response({"error": "Only .ttf or .otf fonts"}, status=400)
    render.USER_FONTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = render.USER_FONTS_DIR / filename
    tmp = dest.with_name(dest.name + ".tmp")
    size = 0
    with open(tmp, "wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FONT_BYTES:
                f.close()
                tmp.unlink()
                return web.json_response({"error": "Font too large (30 MB max)"}, status=413)
            f.write(chunk)
    try:
        from PIL import ImageFont
        ImageFont.truetype(str(tmp), 16)
    except Exception as e:
        tmp.unlink()
        return web.json_response({"error": f"Not a valid font: {e}"}, status=400)
    os.replace(tmp, dest)
    log.info("Font uploaded: %s (%d bytes)", filename, size)
    return web.json_response({"font": filename, "fonts": render.list_fonts()})


@web.middleware
async def _no_cache(request: web.Request, handler):
    """Revalidate on every load so the ingress iframe never serves a stale JS bundle."""
    resp = await handler(request)
    resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


def create_ingress_app() -> web.Application:
    app = web.Application(middlewares=[_no_cache], client_max_size=MAX_FONT_BYTES + 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/displays", api_displays)
    app.router.add_get("/api/displays/{name}.png", display_png)  # preview, under ingress
    app.router.add_get("/api/displays/{name}", api_get_display)
    app.router.add_post("/api/displays", api_create_display)
    app.router.add_put("/api/displays/{name}", api_save_display)
    app.router.add_delete("/api/displays/{name}", api_delete_display)
    app.router.add_post("/api/displays/{name}/rename", api_rename_display)
    app.router.add_post("/api/displays/{name}/render", api_render_now)
    app.router.add_post("/api/preview", api_preview)
    app.router.add_get("/api/preview/{name}.png", api_preview_png)
    app.router.add_get("/api/displays/{name}/helpers", api_helpers_status)
    app.router.add_post("/api/displays/{name}/create-helpers", api_create_helpers)
    app.router.add_post("/api/displays/{name}/sync-helpers", api_sync_helpers)
    app.router.add_post("/api/displays/{name}/adopt-helpers", api_adopt_helpers)
    app.router.add_get("/api/unused-helpers", api_unused_helpers)
    app.router.add_post("/api/cleanup-helpers", api_cleanup_helpers)
    app.router.add_get("/api/entities", api_entities)
    app.router.add_get("/api/hardware", api_hardware)
    app.router.add_get("/api/renderers", api_renderers)
    app.router.add_get("/api/fonts", api_fonts)
    app.router.add_post("/api/fonts", api_upload_font)
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
PREVIEWS: dict[str, bytes] = {}  # transient editor previews of unsaved configs
SYNC: "ha_ws.HAStateSync | None" = None
_pending: set[str] = set()
_debounce_task: "asyncio.Task | None" = None
DEBOUNCE_SECONDS = 0.4


async def _reindex_and_prime(render_names: "list[str] | None" = None) -> None:
    """After a config change: rebuild the entity index, refresh watched states from
    HA (so newly-referenced entities have a current value), and re-render as needed."""
    watched = build_index()
    if SYNC is not None:
        SYNC.watched = watched
        try:
            for e in await ha_ws.fetch_entities():
                if e["entity_id"] in watched:
                    STATES[e["entity_id"]] = e["state"]
        except Exception as ex:
            log.warning("State prime failed: %s", ex)
    for name in render_names or []:
        try:
            render.render_display(name, STATES)
        except Exception as e:
            log.warning("Render %s failed: %s", name, e)


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
    global SYNC
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
    SYNC = sync
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
