"""Ownership ledger — the single authority for which HA helpers inkwell may mutate.

inkwell records every helper it creates (or adopts) here, and only ever *updates* or
*deletes* helpers listed here. Everything else in HA (hand-made helpers, YAML helpers,
integration entities) is read-only to inkwell. If /data is wiped and this file is lost,
inkwell degrades to create-only until helpers are re-adopted — safe by default.
"""

import json
import os
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
LEDGER_PATH = DATA_DIR / "helpers.json"


def load() -> dict:
    try:
        return json.loads(LEDGER_PATH.read_text())
    except Exception:
        return {}


def save(ledger: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2, ensure_ascii=False))
    os.replace(tmp, LEDGER_PATH)


def owns(entity_id: str) -> bool:
    return entity_id in load()


def add(entity_id: str, domain: str, option_set: str | None = None, adopted: bool = False) -> None:
    ledger = load()
    entry = ledger.get(entity_id, {})
    entry.update({"domain": domain})
    entry.setdefault("created", int(time.time()))
    if adopted:
        entry["adopted"] = True
    if option_set:
        entry["option_set"] = option_set
    ledger[entity_id] = entry
    save(ledger)


def remove(entity_id: str) -> None:
    ledger = load()
    if ledger.pop(entity_id, None) is not None:
        save(ledger)
