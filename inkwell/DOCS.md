# Inkwell

Server-side e-ink display renderer with a web UI, running as a Home Assistant add-on.

> **Status:** Phase 1 — add-on skeleton. The UI is a placeholder; rendering, the
> ESP32 image endpoint, and Home Assistant state sync arrive in later phases. See
> `.notes/addon-design.md` in the source repo for the full design.

## Installation

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories** → add
   `https://github.com/rootn/inkwell`.
2. Install **Inkwell**, then **Start** it.
3. Open the **Inkwell** panel from the sidebar — you should see the running page.

## Options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Log verbosity: `debug`, `info`, `warning`, `error`. |

## Ports

| Port | Purpose |
|---|---|
| `5123/tcp` | E-ink image endpoint for ESP32 screens (no auth). Not active until Phase 2. |
