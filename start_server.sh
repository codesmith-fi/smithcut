#!/usr/bin/env bash
cd "$(dirname "$0")"
.venv/bin/uvicorn web.api:app --reload --port 8000
