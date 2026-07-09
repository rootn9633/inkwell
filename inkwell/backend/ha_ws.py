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
API_URL = os.environ.get("HA_API_URL", "http://supervisor/core/api")


async def fetch_entities() -> list[dict]:
    """All HA entities (id, friendly name, state) for the UI entity picker."""
    if not SUPERVISOR_TOKEN:
        return []
    headers = {"Authorization": "Bearer " + SUPERVISOR_TOKEN}
    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL + "/states", headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
    out = []
    for st in data:
        attrs = st.get("attributes") or {}
        e = {
            "entity_id": st["entity_id"],
            "name": attrs.get("friendly_name") or st["entity_id"],
            "state": st.get("state"),
        }
        if st["entity_id"].startswith("input_select.") and isinstance(attrs.get("options"), list):
            e["options"] = attrs["options"]
        out.append(e)
    out.sort(key=lambda e: e["entity_id"])
    return out


async def ws_command(payload: dict, timeout: float = 10.0):
    """Open a short-lived authed websocket, run one command, return its `result`.

    Used for the HA collection APIs (input_boolean/create, input_select/create, …) —
    these are websocket-only, so we can't use the REST proxy for them.
    """
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN not set — cannot talk to HA")
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL, heartbeat=30) as ws:
            msg = await ws.receive_json()
            if msg.get("type") == "auth_required":
                await ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
                msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                raise RuntimeError(f"HA auth failed: {msg}")
            cmd = dict(payload)
            cmd["id"] = 1
            await ws.send_json(cmd)
            while True:
                msg = await asyncio.wait_for(ws.receive_json(), timeout)
                if msg.get("id") == 1 and msg.get("type") == "result":
                    if not msg.get("success", False):
                        raise RuntimeError(str(msg.get("error", "command failed")))
                    return msg.get("result")


async def call_service(domain: str, service: str, entity_id: str):
    """Invoke a HA service on one entity over the websocket (e.g. input_boolean/toggle)."""
    return await ws_command({
        "type": "call_service", "domain": domain, "service": service,
        "target": {"entity_id": entity_id},
    })


async def create_helper(entity_id: str, options=None):
    """Create a storage-backed helper so its entity_id becomes `entity_id`.

    The collection API derives the id from `name` via slugify, so we pass the
    object_id as the name (a valid slug already). Supports input_boolean / input_select.
    """
    domain, _, object_id = entity_id.partition(".")
    if domain == "input_boolean":
        return await ws_command({"type": "input_boolean/create", "name": object_id})
    if domain == "input_select":
        opts = list(options or [])
        if not opts:
            raise ValueError("input_select needs at least one option")
        return await ws_command({"type": "input_select/create", "name": object_id, "options": opts})
    raise ValueError(f"auto-create not supported for domain '{domain}'")


async def update_helper(entity_id: str, options=None, name=None):
    """Update an existing storage helper (collection preserves fields not re-passed).

    HA's `<domain>/update` requires `name` (it's not optional), so default it to the
    object_id — the same slug create_helper uses, keeping entity_id stable.
    """
    domain, _, object_id = entity_id.partition(".")
    payload = {"type": f"{domain}/update", f"{domain}_id": object_id,
               "name": name if name is not None else object_id}
    if options is not None:
        payload["options"] = list(options)
    return await ws_command(payload)


async def delete_helper(entity_id: str):
    domain, _, object_id = entity_id.partition(".")
    return await ws_command({"type": f"{domain}/delete", f"{domain}_id": object_id})


async def list_storage_helpers(domain: str) -> dict:
    """entity_id -> record for storage-backed helpers of a domain (vs YAML, which isn't listed)."""
    out = {}
    for item in (await ws_command({"type": f"{domain}/list"}) or []):
        oid = item.get("id")
        if oid:
            out[f"{domain}.{oid}"] = item
    return out

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
