"""
Resource Warden — v0.2.

Monitors processes and resources. Uses the full v0.2 stack:
  - Pydantic validation for LLM output
  - @resilient decorator for circuit breaker / retry / timeout
  - Heuristic fallback when LLM unavailable
  - Trust Manager + Behavioral Baseline integration
"""

import json
import time
from typing import Any

from langchain_ollama import ChatOllama
from loguru import logger

from intelligence.behavioral_baseline import BehavioralBaseline
from intelligence.trust_manager import TrustManager
from resilience.heuristic_fallback import HeuristicEngine
from resilience.primitives import (
    CircuitBreakerConfig, RetryConfig, resilient
)
from tools.system_probe import SystemProbe
from validation.schemas import (
    ActionType, AgentName, AgentReport, Evidence, Finding,
    LLMVerdict, Location, ScanContext, ThreatLevel, safe_parse_json
)


class ResourceWarden:
    """The eyes on system processes."""

    SYSTEM_PROMPT = """You are the Resource Warden, a Windows internals expert.

Your specialty: detecting malicious processes by analyzing resource patterns,
parent-child relationships, command-line arguments, and load locations.

Reasoning principles:
1. Legitimate Windows processes have predictable parents. svchost.exe spawned
   by Word is almost certainly malicious.
2. Encoded PowerShell (-enc, -EncodedCommand) is almost always malicious.
3. Processes running from %TEMP%, %APPDATA%, Downloads, or Public are suspicious.
4. High CPU + zero network + recent creation = potential cryptominer.
5. Living-off-the-land binaries (rundll32, regsvr32, mshta) abused for execution.
6. Trusted publisher signatures override most heuristics — Microsoft-signed
   binaries running from System32 are NOT malicious based on resource use alone.

You MUST output valid JSON. No prose around it. No markdown fences. No exceptions."""

    DEFAULT_CPU_THRESHOLD = 75.0
    DEFAULT_MEM_THRESHOLD = 70.0

    DEFAULT_SUSPICIOUS_SIGNALS = [
        "powershell.exe -enc",
        "powershell -encoded",
        "powershell -nop -w hidden",
        "rundll32.exe javascript:",
        "regsvr32.exe /s /u /i:",
        "mshta.exe javascript:",
        "certutil -urlcache",
        "bitsadmin /transfer",
        "wmic process call create",
        "schtasks /create",
        "wscript.exe",
        "cscript.exe",
    ]

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model_name: str = "qwen2.5:7b-instruct-q5_K_M",
        temperature: float = 0.2,
        cpu_threshold: float = DEFAULT_CPU_THRESHOLD,
        mem_threshold: float = DEFAULT_MEM_THRESHOLD,
        pre_filter_top_n: int = 5,
        suspicious_signals: list[str] | None = None,
        trust_manager: TrustManager | None = None,
        baseline: BehavioralBaseline | None = None,
    ):
        self.llm = ChatOllama(
            model=model_name,
            base_url=ollama_url,
            temperature=temperature,
            num_ctx=8192,
        )
        self.model_name = model_name
        self.cpu_threshold = cpu_threshold
        self.mem_threshold = mem_threshold
        self.pre_filter_top_n = pre_filter_top_n
        self.suspicious_signals = suspicious_signals or self.DEFAULT_SUSPICIOUS_SIGNALS
        self.probe = SystemProbe()
        self.trust = trust_manager or TrustManager()
        self.baseline = baseline
        self.heuristic = HeuristicEngine()

    @resilient(
        circuit_name="resource_warden_llm",
        timeout_seconds=60.0,
        max_retries=2,
        circuit_config=CircuitBreakerConfig(
            failure_threshold=5,
            timeout_seconds=60.0,
        ),
    )
    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Protected LLM call with circuit breaker, retry, timeout."""
        response = await self.llm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        return response.content if hasattr(response, "content") else str(response)

    async def analyze_with_validation(self, state: ScanContext) -> AgentReport:
        """Main entry point called by LangGraph nodes."""
        start = time.time()
        findings: list[Finding] = []
        errors: list[str] = []

        try:
            # 1. Gather telemetry
            snapshot = self.probe.gather()

            # 2. Update behavioral baseline
            if self.baseline:
                for proc in snapshot.get("processes", []):
                    self.baseline.observe(proc)

            # 3. Pre-filter to top candidates
            candidates = self._pre_filter(snapshot)
            if not candidates:
                logger.info("Resource Warden: no suspicious candidates after pre-filter")
                return AgentReport(
                    agent=AgentName.RESOURCE_WARDEN,
                    findings=[],
                    scan_duration_ms=int((time.time() - start) * 1000),
                )

            logger.info(f"Resource Warden: analyzing {len(candidates)} candidates")

            # 4. For each candidate, try LLM analysis -> fallback to heuristic
            for proc in candidates[:self.pre_filter_top_n]:
                try:
                    finding = await self._analyze_process(proc)
                    if finding:
                        findings.append(finding)
                except Exception as e:
                    logger.warning(
                        f"LLM analysis failed for PID {proc.get('pid')}: {e}. "
                        f"Falling back to heuristic."
                    )
                    errors.append(f"PID {proc.get('pid')}: {type(e).__name__}")
                    # Fallback
                    fallback = self.heuristic.evaluate_process(
                        proc, proc.get("_red_flags", [])
                    )
                    if fallback:
                        findings.append(fallback)

        except Exception as e:
            logger.exception(f"Resource Warden critical failure: {e}")
            errors.append(f"critical: {type(e).__name__}: {e}")

        return AgentReport(
            agent=AgentName.RESOURCE_WARDEN,
            findings=findings,
            scan_duration_ms=int((time.time() - start) * 1000),
            errors_encountered=errors,
        )

    def _pre_filter(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Heuristic pre-filtering — saves LLM calls."""
        candidates = []

        for proc in snapshot.get("processes", []):
            reasons = []

            # 1. Trust check — skip trusted Microsoft-signed system processes early
            trust_verdict = self.trust.evaluate_process(proc)
            if trust_verdict.is_trusted and trust_verdict.trust_level >= 0.9:
                continue  # Skip trusted process
            
            # 2. Impersonation always escalates
            if trust_verdict.impersonation_detected:
                reasons.append("impersonation_detected")

            # 3. Resource thresholds (psutil may emit None on some platforms/PIDs)
            cpu_pct = proc.get("cpu_percent")
            mem_pct = proc.get("memory_percent")
            if (0 if cpu_pct is None else cpu_pct) > self.cpu_threshold:
                reasons.append(f"high_cpu={cpu_pct}")
            if (0 if mem_pct is None else mem_pct) > self.mem_threshold:
                reasons.append(f"high_mem={mem_pct}")

            # 4. Suspicious command-line patterns
            cmdline = (proc.get("cmdline") or "").lower()
            for signal in self.suspicious_signals:
                if signal.lower() in cmdline:
                    reasons.append(f"signal={signal}")
                    break

            # 5. Risky load location
            exe_path = (proc.get("exe") or "").lower()
            risky_dirs = ["\\temp\\", "\\appdata\\", "\\downloads\\", "\\public\\"]
            if any(d in exe_path for d in risky_dirs):
                reasons.append(f"risky_location={exe_path}")

            # 6. Office spawning shells
            parent_name = (proc.get("parent_name") or "").lower()
            child_name = (proc.get("name") or "").lower()
            if child_name in ("powershell.exe", "cmd.exe", "wscript.exe") and parent_name in (
                "winword.exe", "excel.exe", "outlook.exe", "powerpnt.exe"
            ):
                reasons.append(f"office_spawned_shell={parent_name}->{child_name}")

            # 7. Behavioral baseline anomaly
            if self.baseline:
                anomaly = self.baseline.evaluate(proc)
                if anomaly.is_anomaly:
                    reasons.append(f"baseline_anomaly_score={anomaly.score:.2f}")

            if reasons:
                proc["_red_flags"] = reasons
                proc["_trust_level"] = trust_verdict.trust_level
                candidates.append(proc)

        # Sort by severity (more red flags = higher priority)
        candidates.sort(key=lambda p: len(p["_red_flags"]), reverse=True)
        return candidates

    async def _analyze_process(self, proc: dict[str, Any]) -> Finding | None:
        """Use LLM (with validation + fallback) to judge a process."""
        prompt = f"""Analyze this Windows process for malicious behavior.

PROCESS DATA:
{json.dumps(proc, indent=2, default=str, ensure_ascii=False)}

PRE-FILTERED RED FLAGS:
{proc.get('_red_flags', [])}

Output ONLY a JSON object matching this exact schema:
{{
  "is_malicious": boolean,
  "confidence": float between 0 and 1,
  "threat_level": "clean" | "info" | "low" | "medium" | "high" | "critical",
  "title": "short title (3-120 chars)",
  "description": "what this process is doing and why it matters (10-1500 chars)",
  "mitre_techniques": ["T1059.001", "T1055"],
  "recommended_action": "ignore" | "watch" | "alert" | "quarantine" | "terminate" | "block_network",
  "reasoning": "step-by-step justification (10-2000 chars)",
  "confidence_factors": ["factor 1", "factor 2"]
}}

Rules:
- Use lowercase for threat_level and recommended_action.
- For low confidence + non-malicious, prefer "ignore" or "watch" with threat_level "info" or "low".
- For high confidence malicious, prefer "quarantine" or "terminate" with "high" or "critical".
- mitre_techniques MUST be valid MITRE format (T#### or T####.###).
- Description must explain WHAT, not just WHY.
"""

        # Call LLM (protected by @resilient)
        try:
            raw = await self._call_llm(self.SYSTEM_PROMPT, prompt)
        except Exception as e:
            logger.warning(f"LLM call failed for PID {proc.get('pid')}: {e}")
            raise

        # Parse + validate
        parsed = safe_parse_json(raw)
        if not parsed:
            logger.warning(f"Could not parse JSON from LLM output: {raw[:200]}")
            return None

        try:
            verdict = LLMVerdict.model_validate(parsed)
        except Exception as e:
            logger.warning(f"LLM verdict failed validation: {e}")
            return None

        # Skip non-findings
        if not verdict.is_malicious and verdict.confidence < 0.5:
            return None

        # Build Finding
        try:
            location = Location(
                pid=proc.get("pid"),
                process_name=proc.get("name"),
                file_path=proc.get("exe"),
            )
        except ValueError:
            return None

        try:
            return Finding(
                agent_name=AgentName.RESOURCE_WARDEN,
                threat_level=ThreatLevel(verdict.threat_level),
                title=verdict.title,
                description=verdict.description,
                location=location,
                evidence=Evidence(
                    raw_data={"process": proc},
                    red_flags=proc.get("_red_flags", []),
                    reasoning=verdict.reasoning,
                ),
                confidence=verdict.confidence,
                recommended_action=ActionType(verdict.recommended_action),
                mitre_techniques=verdict.mitre_techniques,
            )
        except ValueError as e:
            logger.warning(f"Finding construction rejected: {e}")
            return None
