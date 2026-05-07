"""
Arbitrator — The Council Leader.

Responsibilities:
  1. Aggregate findings from all agents
  2. Identify conflicts (Agent A says malicious, Agent B says clean)
  3. Apply correlation rules (multi-agent agreement boosts confidence)
  4. Suppress likely false positives
  5. Produce a unified, user-facing decision in both Arabic and English
"""

import json
import time
from typing import Any

from langchain_ollama import ChatOllama
from loguru import logger

from resilience.primitives import (
    CircuitBreakerConfig, RetryConfig, resilient
)
from validation.schemas import (
    ActionType, AgentName, AgentReport, CouncilDecision, Finding,
    ScanContext, ThreatLevel
)


class ArbitratorAgent:
    """LLM-powered final decision maker."""

    SYSTEM_PROMPT = """You are the Arbitrator of a security council. Three specialist agents
report to you: Resource Warden (processes), Cyber Analyst (files), Traffic Observer (network).

Your job:
1. WEIGH evidence — multi-agent agreement is strong; single-agent low-confidence is weak.
2. SUPPRESS false positives — common Microsoft binaries, signed software, well-known processes.
3. CONFIRM real threats — when 2+ agents independently flag the same PID/file, it's almost certainly real.
4. EXPLAIN clearly — produce summaries in both Arabic and English.

Decision principles:
- A finding flagged by 2+ agents: confidence boost +0.2, level upgraded if needed
- A single agent finding with confidence < 0.5: suppress unless it's CRITICAL
- Conflicting verdicts: trust the agent with higher confidence + more evidence
- Always be honest about uncertainty in your summary

You MUST output valid JSON matching the schema. No prose outside JSON."""

    def __init__(
        self,
        model_name: str = "qwen2.5:7b-instruct-q5_K_M",
        ollama_url: str = "http://localhost:11434",
        temperature: float = 0.2,
    ):
        self.llm = ChatOllama(
            model=model_name,
            base_url=ollama_url,
            temperature=temperature,
            num_ctx=8192,
        )
        self.model_name = model_name

    @resilient(
        circuit_name="arbitrator_llm",
        timeout_seconds=90.0,
        max_retries=2,
        circuit_config=CircuitBreakerConfig(
            failure_threshold=3,
            timeout_seconds=60.0,
        ),
    )
    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """LLM call protected by circuit breaker, retry, and timeout."""
        response = await self.llm.ainvoke(messages)
        return response.content if hasattr(response, "content") else str(response)

    async def deliberate(self, state: ScanContext) -> CouncilDecision:
        """Main entry point — produces final CouncilDecision."""
        start_time = time.time()

        all_findings = self._collect_findings(state)
        correlations = state.shared_evidence.get("correlations", {})

        if not all_findings:
            return self._clean_decision()

        # Apply correlation boosts before LLM deliberation
        boosted_findings = self._apply_correlation_boost(all_findings, correlations)

        # Build the deliberation prompt
        deliberation_prompt = self._build_prompt(boosted_findings, correlations, state)

        try:
            from validation.validator import LLMValidator
            from validation.schemas import safe_parse_json

            raw_output = await self._call_llm([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": deliberation_prompt},
            ])

            verdict_data = safe_parse_json(raw_output)
            if not verdict_data:
                logger.warning("Arbitrator LLM returned unparseable output. Using rule-based fallback.")
                return self._rule_based_decision(boosted_findings, correlations)

            decision = self._build_decision_from_llm(
                verdict_data, boosted_findings, all_findings, correlations
            )

        except Exception as e:
            logger.error(f"Arbitrator LLM failed ({e}), using rule-based decision.")
            decision = self._rule_based_decision(boosted_findings, correlations)

        elapsed = (time.time() - start_time) * 1000
        logger.info(
            f"Arbitrator decision: {decision.overall_threat_level.value} "
            f"(confidence={decision.confidence:.2f}, took {elapsed:.0f}ms)"
        )
        return decision

    # ============================================================
    # Helpers
    # ============================================================

    def _collect_findings(self, state: ScanContext) -> list[Finding]:
        return [
            f for report in state.reports.values() for f in report.findings
        ]

    def _apply_correlation_boost(
        self, findings: list[Finding], correlations: dict[str, Any]
    ) -> list[Finding]:
        """
        Boost confidence for findings that multiple agents flagged.
        Returns NEW Finding objects (immutable).
        """
        multi_pids = set(correlations.get("multi_agent_pids", []))
        multi_files = set(correlations.get("multi_agent_files", []))

        boosted = []
        for f in findings:
            should_boost = (
                (f.location.pid in multi_pids if f.location.pid else False)
                or (f.location.file_path in multi_files if f.location.file_path else False)
            )

            if should_boost:
                # Build a copy with boosted confidence and upgraded level
                new_confidence = min(1.0, f.confidence + 0.2)
                new_level = self._upgrade_level(f.threat_level)
                boosted.append(
                    f.model_copy(update={
                        "confidence": new_confidence,
                        "threat_level": new_level,
                        "evidence": f.evidence.model_copy(update={
                            "red_flags": f.evidence.red_flags + ["multi_agent_correlation"],
                        }),
                    })
                )
            else:
                boosted.append(f)

        return boosted

    @staticmethod
    def _upgrade_level(level: ThreatLevel) -> ThreatLevel:
        """Bump threat level up by one notch."""
        upgrade_map = {
            ThreatLevel.CLEAN: ThreatLevel.LOW,
            ThreatLevel.INFO: ThreatLevel.LOW,
            ThreatLevel.LOW: ThreatLevel.MEDIUM,
            ThreatLevel.MEDIUM: ThreatLevel.HIGH,
            ThreatLevel.HIGH: ThreatLevel.CRITICAL,
            ThreatLevel.CRITICAL: ThreatLevel.CRITICAL,
        }
        return upgrade_map[level]

    def _build_prompt(
        self,
        findings: list[Finding],
        correlations: dict[str, Any],
        state: ScanContext,
    ) -> str:
        findings_summary = [
            {
                "agent": f.agent_name.value,
                "title": f.title,
                "level": f.threat_level.value,
                "confidence": round(f.confidence, 2),
                "location": f.location.model_dump(exclude_none=True),
                "action": f.recommended_action.value,
                "mitre": f.mitre_techniques,
                "red_flags_count": len(f.evidence.red_flags),
            }
            for f in findings
        ]

        return f"""Deliberate on these findings from the council and produce a unified decision.

FINDINGS ({len(findings)} total):
{json.dumps(findings_summary, indent=2, ensure_ascii=False)}

CORRELATIONS:
- PIDs flagged by multiple agents: {correlations.get('multi_agent_pids', [])}
- Files flagged by multiple agents: {correlations.get('multi_agent_files', [])}

ITERATION: {state.iteration}/{state.max_iterations}

Output a JSON object with this exact schema:
{{
  "overall_threat_level": "clean" | "info" | "low" | "medium" | "high" | "critical",
  "confidence": 0.0 to 1.0,
  "consensus_reached": true | false,
  "endorsed_finding_ids": ["id1", "id2"],
  "suppressed_finding_ids": ["id3"],
  "recommended_actions": ["watch", "quarantine"],
  "user_summary_ar": "ملخص عربي مختصر للمستخدم — جملتين كحد أقصى",
  "user_summary_en": "Brief user summary in English — 2 sentences max",
  "technical_report": "Detailed technical explanation referencing specific findings"
}}

Rules:
- consensus_reached: true if your decision is clear, false if you need another iteration
- Endorse findings with strong evidence; suppress low-confidence singles
- summaries must be honest about uncertainty
- DO NOT invent threats — only reason about what was reported"""

    def _build_decision_from_llm(
        self,
        verdict: dict[str, Any],
        boosted_findings: list[Finding],
        all_findings: list[Finding],
        correlations: dict[str, Any],
    ) -> CouncilDecision:
        """Convert LLM JSON output into CouncilDecision."""
        endorsed_ids = set(verdict.get("endorsed_finding_ids", []))
        suppressed_ids = set(verdict.get("suppressed_finding_ids", []))

        # If LLM didn't specify, endorse all high-confidence findings
        if not endorsed_ids and not suppressed_ids:
            endorsed_ids = {f.finding_id for f in boosted_findings if f.confidence >= 0.6}
            suppressed_ids = {f.finding_id for f in boosted_findings if f.confidence < 0.6}

        primary = [f for f in boosted_findings if f.finding_id in endorsed_ids]
        suppressed = [f for f in boosted_findings if f.finding_id in suppressed_ids]

        try:
            level = ThreatLevel(verdict["overall_threat_level"])
        except (KeyError, ValueError):
            level = max((f.threat_level for f in primary), key=lambda t: t.numeric, default=ThreatLevel.CLEAN)

        try:
            actions = [ActionType(a) for a in verdict.get("recommended_actions", [])]
        except ValueError:
            actions = list({f.recommended_action for f in primary})

        voting = {lvl.value: sum(1 for f in all_findings if f.threat_level == lvl) for lvl in ThreatLevel}

        return CouncilDecision(
            overall_threat_level=level,
            confidence=float(verdict.get("confidence", 0.5)),
            consensus_reached=bool(verdict.get("consensus_reached", True)),
            voting_summary=voting,
            primary_findings=primary,
            suppressed_findings=suppressed,
            recommended_actions=actions,
            user_summary_ar=verdict.get("user_summary_ar", "تم إكمال الفحص."),
            user_summary_en=verdict.get("user_summary_en", "Scan complete."),
            technical_report=verdict.get("technical_report", "No detailed report available."),
        )

    def _rule_based_decision(
        self, findings: list[Finding], correlations: dict[str, Any]
    ) -> CouncilDecision:
        """Fallback when LLM is unavailable — pure logic."""
        primary = [f for f in findings if f.confidence >= 0.6]
        suppressed = [f for f in findings if f.confidence < 0.6]

        max_level = max(
            (f.threat_level for f in primary),
            key=lambda t: t.numeric,
            default=ThreatLevel.CLEAN,
        )
        avg_confidence = (
            sum(f.confidence for f in primary) / len(primary) if primary else 0.7
        )

        actions = list({f.recommended_action for f in primary})
        voting = {lvl.value: sum(1 for f in findings if f.threat_level == lvl) for lvl in ThreatLevel}

        return CouncilDecision(
            overall_threat_level=max_level,
            confidence=avg_confidence,
            consensus_reached=True,
            voting_summary=voting,
            primary_findings=primary,
            suppressed_findings=suppressed,
            recommended_actions=actions,
            user_summary_ar=(
                f"تم اكتشاف {len(primary)} تهديد بمستوى {max_level.value}. "
                f"التحليل تم بمنطق القواعد بدون نموذج لغوي."
            ),
            user_summary_en=(
                f"Detected {len(primary)} threats at level {max_level.value}. "
                f"Decision made with rule-based logic (LLM unavailable)."
            ),
            technical_report=(
                f"Rule-based arbitration. Total findings: {len(findings)}. "
                f"Endorsed: {len(primary)}. Suppressed: {len(suppressed)}. "
                f"Multi-agent correlations: {len(correlations.get('multi_agent_pids', []))} PIDs, "
                f"{len(correlations.get('multi_agent_files', []))} files."
            ),
        )

    def _clean_decision(self) -> CouncilDecision:
        return CouncilDecision(
            overall_threat_level=ThreatLevel.CLEAN,
            confidence=0.85,
            consensus_reached=True,
            voting_summary={ThreatLevel.CLEAN.value: 3},
            primary_findings=[],
            recommended_actions=[],
            user_summary_ar="جهازك آمن. لم يتم اكتشاف أي تهديدات.",
            user_summary_en="Your device is safe. No threats detected.",
            technical_report="All three agents reported zero findings.",
        )
