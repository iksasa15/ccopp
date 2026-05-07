"""
Local FastAPI server for the React dashboard.

Run from the project root (so ./config/settings.yaml resolves):

    uvicorn api.app:app --host 127.0.0.1 --port 8765

Then start the Vite dev server in ./web (npm run dev).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from run import (
    CouncilSystem,
    execute_baseline_stats,
    execute_list_quarantine,
    execute_scan_archive,
    execute_scan_system,
    execute_verify_audit,
    load_config,
    setup_logging,
)

_scan_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    setup_logging(config)
    system = CouncilSystem(config)
    await system.initialize()
    app.state.system = system
    yield
    await system.shutdown()


app = FastAPI(title="Council of Agents API", lifespan=lifespan)


def _wire_cors(application: FastAPI, config: dict[str, Any]) -> None:
    api_cfg = config.get("api", {})
    origins = api_cfg.get("cors_origins", ["http://localhost:5173", "http://127.0.0.1:5173"])
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


_cfg_at_import = load_config()
_wire_cors(app, _cfg_at_import)


class ArchiveBody(BaseModel):
    path: str = Field(..., min_length=1, description="Absolute or relative path to archive")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/commands")
async def commands_help():
    return {
        "cli": "python run.py <command> [args]",
        "commands": [
            {"name": "scan-system", "method": "POST", "path": "/api/scan-system"},
            {"name": "scan-archive", "method": "POST", "path": "/api/scan-archive", "body": {"path": "<file>"}},
            {"name": "verify-audit", "method": "GET", "path": "/api/verify-audit"},
            {"name": "baseline-stats", "method": "GET", "path": "/api/baseline-stats"},
            {"name": "list-quarantine", "method": "GET", "path": "/api/list-quarantine"},
        ],
    }


@app.post("/api/scan-system")
async def api_scan_system():
    system: CouncilSystem = app.state.system
    async with _scan_lock:
        outcome = await execute_scan_system(system, show_progress=False)
        return outcome.as_json()


@app.post("/api/scan-archive")
async def api_scan_archive(body: ArchiveBody):
    system: CouncilSystem = app.state.system
    async with _scan_lock:
        return await execute_scan_archive(system, body.path)


@app.get("/api/verify-audit")
async def api_verify_audit():
    system: CouncilSystem = app.state.system
    async with _scan_lock:
        return await execute_verify_audit(system)


@app.get("/api/baseline-stats")
async def api_baseline_stats():
    system: CouncilSystem = app.state.system
    async with _scan_lock:
        return await execute_baseline_stats(system)


@app.get("/api/list-quarantine")
async def api_list_quarantine():
    system: CouncilSystem = app.state.system
    async with _scan_lock:
        return await execute_list_quarantine(system)
