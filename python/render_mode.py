#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

DEFAULT_RENDER_MODE = "server"


def normalize_render_mode(value: Any, fallback: str = DEFAULT_RENDER_MODE) -> str:
    if not isinstance(value, str):
        return fallback

    normalized = value.strip().lower()
    if normalized in {"server", "client"}:
        return normalized
    return fallback


def should_render_on_server(render_mode: str) -> bool:
    return render_mode == "server"
