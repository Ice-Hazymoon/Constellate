from __future__ import annotations

import uvicorn

from .app import app
from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        timeout_keep_alive=settings.idle_timeout_seconds,
    )


if __name__ == "__main__":
    main()
