#!/usr/bin/env python3
"""Inkwell add-on — aiohttp application.

Phase 1: serves a minimal Alpine "hello" page under Home Assistant ingress and a
health endpoint. Rendering, the image port, and the HA websocket arrive in later
phases; this module is the entrypoint they'll grow from.
"""

import logging
import os
from pathlib import Path

import jinja2
from aiohttp import web

APP_DIR = Path(__file__).parent
FRONTEND_DIR = APP_DIR.parent / "frontend"
TEMPLATES_DIR = FRONTEND_DIR / "templates"
STATIC_DIR = FRONTEND_DIR / "static"

INGRESS_PORT = int(os.environ.get("INGRESS_PORT", "8099"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("inkwell")

_jinja = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
)


def ingress_base(request: web.Request) -> str:
    """Base path the UI is served under. HA sets X-Ingress-Path behind ingress;
    empty when hit directly. Asset/API URLs in templates are prefixed with this."""
    return request.headers.get("X-Ingress-Path", "")


async def index(request: web.Request) -> web.Response:
    html = _jinja.get_template("index.html").render(base=ingress_base(request))
    return web.Response(text=html, content_type="text/html")


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")
    return app


def main() -> None:
    log.info("Inkwell starting; serving ingress UI on port %d", INGRESS_PORT)
    web.run_app(create_app(), host="0.0.0.0", port=INGRESS_PORT, print=None)


if __name__ == "__main__":
    main()
