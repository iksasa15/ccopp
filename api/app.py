"""
Local FastAPI server for the React dashboard.

Run from the project root (so ./config/settings.yaml resolves):

    uvicorn api.app:app --host 127.0.0.1 --port 8765

Then start the Vite dev server in ./web (npm run dev).
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
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

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_COA_PROJECT = Path(os.getenv("COA_PROJECT_DIR", str(_PROJECT_ROOT / "COA" / "COA_Project"))).expanduser().resolve()


def _coa_flask_base() -> str:
    return os.getenv("COA_FLASK_URL", "http://127.0.0.1:5050").rstrip("/")


async def _probe_coa_flask() -> dict[str, Any]:
    base = _coa_flask_base()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base}/api/health")
            body: Any
            try:
                body = r.json()
            except Exception:
                body = {"raw": (r.text or "")[:500]}
            return {
                "reachable": r.status_code == 200,
                "status_code": r.status_code,
                "url": f"{base}/api/health",
                "body": body,
            }
    except Exception as exc:
        return {"reachable": False, "url": f"{base}/api/health", "error": str(exc)}


class ArchiveBody(BaseModel):
    path: str = Field(..., min_length=1, description="Absolute or relative path to archive")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/integrations")
async def api_integrations():
    """Manifest for merged Council + COA (Flask) stack."""
    coa_web_api = _COA_PROJECT / "web_api.py"
    coa_available = coa_web_api.is_file()
    probe = await _probe_coa_flask() if coa_available else {"reachable": False, "error": "COA web_api.py not found"}

    return {
        "council": {
            "fastapi": True,
            "default_url": "http://127.0.0.1:8765",
            "endpoints": [
                "/api/health",
                "/api/scan-system",
                "/api/scan-archive",
                "/api/verify-audit",
                "/api/baseline-stats",
                "/api/list-quarantine",
                "/api/commands",
            ],
        },
        "coa": {
            "project_dir": str(_COA_PROJECT),
            "web_api_path": str(coa_web_api),
            "available": coa_available,
            "flask_base_url": _coa_flask_base(),
            "flask_probe": probe,
            "endpoints_via_vite_proxy": [
                "/coa-api/health",
                "/coa-api/health/ollama",
                "/coa-api/health/llm",
                "/coa-api/scan (POST)",
                "/coa-api/last/defense-context",
                "/coa-api/last/mitre-deep",
                "/coa-api/last/ot-ics",
                "/coa-api/reports/txt",
                "/coa-api/reports/html",
            ],
        },
        "vite": {
            "default_url": "http://127.0.0.1:5173",
            "note": "Proxy /api -> 8765, /coa-api -> 5050",
        },
    }


@app.get("/api/coa/health-proxy")
async def api_coa_health_proxy():
    """Lightweight check that COA Flask is up (same as integrations.coa.flask_probe)."""
    return await _probe_coa_flask()


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
