#!/usr/bin/with-contenv bashio
# Inkwell add-on entrypoint. Reads add-on options and launches the aiohttp app.

export LOG_LEVEL="$(bashio::config 'log_level' 'info')"
export INGRESS_PORT=8099
# Blank => the backend auto-detects the host's LAN IP for generated firmware.
export RENDERER_URL="$(bashio::config 'renderer_url' '')"

bashio::log.info "Starting Inkwell (log_level=${LOG_LEVEL})"
exec python3 /app/backend/app.py
