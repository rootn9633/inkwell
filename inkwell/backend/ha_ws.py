"""Home Assistant websocket state sync.

Connects to the Supervisor-proxied HA websocket using the add-on's SUPERVISOR_TOKEN,
loads current states, subscribes to `state_changed`, and calls a callback whenever a
*watched* entity changes. This replaces the old rest_command + automation layer — the
add-on now reacts to HA state directly.
"""

import asyncio
import logging
import os
import re

import aiohttp

log = logging.getLogger("inkwell.ha_ws")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
WS_URL = os.environ.get("HA_WS_URL", "ws://supervisor/core/websocket")

# HA entity_id: lowercase domain.object_id. Display configs only contain entity ids
# in this exact shape (font filenames, hardware names, menu text don't match), so a
# recursive scan for this pattern reliably yields a display's watched entities.
ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def extract_entities(config) -> set[str]:
    """Every entity_id referenced anywhere in a display config."""
    found: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and ENTITY_RE.match(o):
            found.add(o)

    walk(config)
    return found


class HAStateSync:
    def __init__(self, watched: set[str], on_change):
        # on_change(states: dict[str, str], changed: set[str])
        self.watched = set(watched)
        self.on_change = on_change
        self.states: dict[str, str] = {}
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _await_result(self, ws, msg_id):
        while True:
            msg = await ws.receive_json()
            if msg.get("id") == msg_id and msg.get("type") == "result":
                if not msg.get("success", True):
                    raise RuntimeError(f"HA command {msg_id} failed: {msg.get('error')}")
                return msg.get("result")

    async def _connect_and_listen(self):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(WS_URL, heartbeat=30) as ws:
                # --- auth handshake ---
                msg = await ws.receive_json()
                if msg.get("type") == "auth_required":
                    await ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
                    msg = await ws.receive_json()
                if msg.get("type") != "auth_ok":
                    raise RuntimeError(f"HA auth failed: {msg}")
                log.info("HA websocket authenticated")

                # --- seed current states, trigger an initial render ---
                gid = self._next_id()
                await ws.send_json({"id": gid, "type": "get_states"})
                for st in await self._await_result(ws, gid):
                    eid = st["entity_id"]
                    if eid in self.watched:
                        self.states[eid] = st["state"]
                log.info("Loaded initial states (%d/%d watched present)",
                         len(self.states), len(self.watched))
                self.on_change(dict(self.states), set(self.watched))

                # --- subscribe to future changes ---
                sid = self._next_id()
                await ws.send_json({"id": sid, "type": "subscribe_events",
                                    "event_type": "state_changed"})
                await self._await_result(ws, sid)
                log.info("Subscribed to state_changed")

                async for msg in ws:
                    if msg.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    data = msg.json()
                    if data.get("type") != "event":
                        continue
                    ev = data["event"]["data"]
                    eid = ev.get("entity_id")
                    if eid not in self.watched:
                        continue
                    new_state = (ev.get("new_state") or {}).get("state", "unavailable")
                    if self.states.get(eid) == new_state:
                        continue
                    self.states[eid] = new_state
                    log.info("State change: %s -> %s", eid, new_state)
                    self.on_change(dict(self.states), {eid})

    async def run(self):
        if not SUPERVISOR_TOKEN:
            log.warning("SUPERVISOR_TOKEN not set — HA state sync disabled")
            return
        while True:
            try:
                await self._connect_and_listen()
            except Exception as e:
                log.warning("HA websocket error (%s); reconnecting in 5s", e)
            await asyncio.sleep(5)
