#!/usr/bin/env bash
cd "$(dirname "$0")"
trap 'stty sane 2>/dev/null || true' EXIT INT TERM

# Älä seuraa workspace/-kirjoituksia (preview mp4, frames, thumbnails) —
# muuten uvicorn --reload boottaa palvelimen kesken FFmpeg-renderöinnin.
.venv/bin/uvicorn web.api:app --reload --port 8000 \
  --reload-dir agent \
  --reload-dir compiler \
  --reload-dir controller \
  --reload-dir mcp_server \
  --reload-dir model \
  --reload-dir web \
  --reload-exclude 'workspace/*' \
  --reload-exclude '*.mp4' \
  --reload-exclude '*.jpg' \
  --reload-exclude '*.json'
