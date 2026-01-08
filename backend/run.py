#!/usr/bin/env python3
"""
Скрипт для запуску backend сервера
"""
import os
import uvicorn

if __name__ == "__main__":
    # IMPORTANT (Windows): reload spawns an extra process and can easily double RAM usage.
    # Default: reload disabled. Enable only when actively developing the backend.
    reload = (os.getenv("UVICORN_RELOAD") or "0").lower() in ("1", "true", "yes")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=reload,
        log_level="info"
    )

