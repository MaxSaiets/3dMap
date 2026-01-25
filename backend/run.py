#!/usr/bin/env python3
"""
Скрипт для запуску backend сервера
"""
import os
import uvicorn

if __name__ == "__main__":
    # IMPORTANT (Windows): reload spawns an extra process and can easily double RAM usage.
    # Default: reload disabled. Enable only when actively developing the backend.
    reload = True # Enabled for development
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=reload,
        log_level="info",
        loop="asyncio" # FIX: Use asyncio selector loop instead of proactor (default on Py3.8+ Win) to avoid [WinError 10054]
    )

