"""
Local FastAPI server for the React dashboard.

Run from the project root (so ./config/settings.yaml resolves):

    uvicorn api.app:app --host 127.0.0.1 --port 8765

Then start the Vite dev server in ./web (npm run dev).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger("api.app")

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


class ScanSystemBody(BaseModel):
    mode: Literal["quick", "deep"] = "deep"


# ============================================================
# Deep file analysis helpers (LLM-driven)
# ============================================================

_DEEP_ANALYSIS_MAX_FILES = 8
_DEEP_ANALYSIS_CONCURRENCY = 3
_DEEP_ANALYSIS_TIMEOUT_S = 30.0
_VERDICT_PATTERN = re.compile(r"\b(suspicious|benign|unknown)\b", re.IGNORECASE)
_JSON_BLOCK_PATTERN = re.compile(r"\{[\s\S]*\}")


def _extract_paths_for_deep_analysis(payload: dict[str, Any]) -> list[str]:
    """Pick the most suspicious file paths from a Council scan payload."""
    seen: set[str] = set()
    ordered: list[str] = []

    fs = payload.get("filesystem_scan") or {}
    findings = fs.get("findings") if isinstance(fs, dict) else None
    if isinstance(findings, list):
        for f in findings:
            if not isinstance(f, dict):
                continue
            p = f.get("path")
            if isinstance(p, str) and p not in seen:
                seen.add(p)
                ordered.append(p)
            if len(ordered) >= _DEEP_ANALYSIS_MAX_FILES:
                return ordered

    decision = payload.get("decision")
    if isinstance(decision, dict):
        primary = decision.get("primary_findings")
        if isinstance(primary, list):
            for finding in primary:
                if not isinstance(finding, dict):
                    continue
                resource = finding.get("affected_resource") or finding.get("path")
                if isinstance(resource, str) and resource.startswith("/") and resource not in seen:
                    seen.add(resource)
                    ordered.append(resource)
                if len(ordered) >= _DEEP_ANALYSIS_MAX_FILES:
                    return ordered

    return ordered


def _file_metadata(path: str) -> dict[str, Any]:
    p = Path(path).expanduser()
    meta: dict[str, Any] = {"exists": False}
    try:
        if p.exists():
            st = p.stat()
            meta = {
                "exists": True,
                "is_file": p.is_file(),
                "size_bytes": int(st.st_size),
                "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                "suffix": p.suffix.lower(),
            }
    except Exception as exc:
        meta = {"exists": False, "error": str(exc)}
    return meta


def _parse_llm_verdict(raw: str) -> tuple[str, str]:
    """Try to extract (verdict, rationale) from LLM output."""
    if not raw:
        return "unknown", ""
    block = _JSON_BLOCK_PATTERN.search(raw)
    if block:
        try:
            obj = json.loads(block.group(0))
            verdict = str(obj.get("verdict", "unknown")).strip().lower()
            rationale = str(obj.get("rationale", "")).strip()
            if verdict not in {"suspicious", "benign", "unknown"}:
                verdict = "unknown"
            return verdict, rationale or raw.strip()
        except Exception:
            pass
    m = _VERDICT_PATTERN.search(raw)
    verdict = m.group(1).lower() if m else "unknown"
    return verdict, raw.strip()


async def _deep_analyze_paths(
    paths: list[str],
    *,
    ollama_url: str,
    model_name: str,
) -> dict[str, Any]:
    """Run a per-file LLM check to confirm the deep scan actually used the model."""
    if not paths:
        return {
            "note": "no candidate files",
            "model": model_name,
            "results": [],
        }

    started = time.monotonic()
    try:
        from langchain_ollama import ChatOllama  # type: ignore
    except Exception:
        try:
            from langchain_community.chat_models import ChatOllama  # type: ignore
        except Exception as exc:
            logger.warning(f"deep_file_analysis: ChatOllama import failed: {exc}")
            return {
                "error": f"ChatOllama unavailable: {exc}",
                "model": model_name,
                "results": [],
            }

    try:
        llm = ChatOllama(
            model=model_name,
            base_url=ollama_url,
            temperature=0.2,
            num_ctx=4096,
        )
    except Exception as exc:
        logger.warning(f"deep_file_analysis: ChatOllama init failed: {exc}")
        return {
            "error": f"ChatOllama init failed: {exc}",
            "model": model_name,
            "results": [],
        }

    sem = asyncio.Semaphore(_DEEP_ANALYSIS_CONCURRENCY)
    system_prompt = (
        "You are a security analyst. Given a single file path and its metadata, "
        "decide whether the file is suspicious, benign, or unknown. "
        "Reply ONLY with strict JSON: "
        '{"verdict": "suspicious|benign|unknown", "rationale": "<short reason in Arabic>"}.'
    )

    async def analyze_one(path: str) -> dict[str, Any]:
        meta = _file_metadata(path)
        user_prompt = (
            f"path: {path}\n"
            f"metadata: {json.dumps(meta, ensure_ascii=False)}\n"
            "Return JSON only."
        )
        async with sem:
            try:
                response = await asyncio.wait_for(
                    llm.ainvoke(
                        [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ]
                    ),
                    timeout=_DEEP_ANALYSIS_TIMEOUT_S,
                )
                raw = response.content if hasattr(response, "content") else str(response)
                verdict, rationale = _parse_llm_verdict(str(raw))
                return {
                    "path": path,
                    "metadata": meta,
                    "verdict": verdict,
                    "rationale": rationale,
                    "model": model_name,
                }
            except asyncio.TimeoutError:
                return {
                    "path": path,
                    "metadata": meta,
                    "verdict": "unknown",
                    "rationale": "LLM timeout",
                    "model": model_name,
                    "error": "timeout",
                }
            except Exception as exc:
                return {
                    "path": path,
                    "metadata": meta,
                    "verdict": "unknown",
                    "rationale": f"LLM error: {exc}",
                    "model": model_name,
                    "error": str(exc),
                }

    results = await asyncio.gather(*[analyze_one(p) for p in paths])
    elapsed = time.monotonic() - started
    suspicious = sum(1 for r in results if r.get("verdict") == "suspicious")
    logger.info(
        f"deep_file_analysis: {len(results)} files analyzed via LLM "
        f"({suspicious} suspicious) in {elapsed:.1f}s"
    )
    return {
        "model": model_name,
        "ollama_url": ollama_url,
        "files_analyzed": len(results),
        "suspicious_count": suspicious,
        "duration_seconds": round(elapsed, 2),
        "results": results,
    }


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
            {
                "name": "scan-system",
                "method": "POST",
                "path": "/api/scan-system",
                "body": {"mode": "quick|deep"},
            },
            {"name": "scan-archive", "method": "POST", "path": "/api/scan-archive", "body": {"path": "<file>"}},
            {"name": "verify-audit", "method": "GET", "path": "/api/verify-audit"},
            {"name": "baseline-stats", "method": "GET", "path": "/api/baseline-stats"},
            {"name": "list-quarantine", "method": "GET", "path": "/api/list-quarantine"},
        ],
    }


@app.post("/api/scan-system")
async def api_scan_system(body: ScanSystemBody):
    system: CouncilSystem = app.state.system
    async with _scan_lock:
        if body.mode == "quick":
            # Fast bounded scan for demos: lightweight filesystem heuristic + fixed duration.
            from tools.system_probe import SystemProbe

            started = time.monotonic()
            fs = SystemProbe(warm_cpu=False).scan_filesystem_quick(max_files=3500, max_findings=20)
            threats = [
                {
                    "severity": "MEDIUM",
                    "confidence": "LOW",
                    "score": 45,
                    "type": "File Threat",
                    "source": str(f.get("path", "unknown")),
                    "details": f"Detected: {', '.join(f.get('signals', []))}",
                    "signals": f.get("signals", []),
                    "recommended_action": f.get("recommended_action", "investigate"),
                }
                for f in fs.get("findings", [])
            ]
            elapsed = time.monotonic() - started
            if elapsed < 10:
                await asyncio.sleep(10 - elapsed)

            return {
                "ok": True,
                "scan_mode": "quick",
                "duration_seconds": 10,
                "total_threats": len(threats),
                "critical": 0,
                "high": 0,
                "medium": len(threats),
                "low": 0,
                "high_confidence_threats": 0,
                "threats": threats,
                "filesystem_scan": fs,
            }

        outcome = await execute_scan_system(system, show_progress=False)
        payload = outcome.as_json()

        paths = _extract_paths_for_deep_analysis(payload)
        llm_cfg = system.config.get("llm", {}) if hasattr(system, "config") else {}
        ollama_url = llm_cfg.get("ollama_url", "http://localhost:11434")
        model_name = llm_cfg.get("primary_model", "qwen2.5:7b-instruct-q5_K_M")

        if paths:
            logger.info(f"deep scan: dispatching {len(paths)} files to LLM ({model_name})")
            payload["deep_file_analysis"] = await _deep_analyze_paths(
                paths,
                ollama_url=ollama_url,
                model_name=model_name,
            )
        else:
            payload["deep_file_analysis"] = {
                "note": "لا توجد ملفات مرشحة لتحليل LLM",
                "model": model_name,
                "results": [],
            }

        payload["scan_mode"] = "deep"
        return payload


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
