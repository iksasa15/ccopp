"""
Council Graph — LangGraph orchestration of the agents.

LangGraph requires that channels updated by multiple parallel nodes use
Annotated[T, reducer]. We use TypedDict for the graph state, with reducers
that merge dict updates from concurrent agent nodes.

Flow:
    [entry] → fan-out: [resource_warden, cyber_analyst, traffic_observer]
              → fan-in: [cross_reference] → [arbitrator]
              → conditional: iterate or finalize
"""

import operator
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from loguru import logger

from validation.schemas import (
    AgentName, AgentReport, CouncilDecision, Finding, ThreatLevel
)


# ============================================================
# Graph State — TypedDict for LangGraph compatibility
# ============================================================

def merge_reports(
    old: dict[str, AgentReport], new: dict[str, AgentReport]
) -> dict[str, AgentReport]:
    """Reducer for the reports channel — merges dicts from parallel agents."""
    return {**old, **new}


def merge_dicts(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Generic dict merger for shared_evidence."""
    return {**old, **new}


class CouncilState(TypedDict, total=False):
    """LangGraph state. Each key uses a reducer where parallel updates happen."""
    scan_id: str
    scan_type: str
    target_path: str | None

    # PARALLEL UPDATES — need reducer
    reports: Annotated[dict[str, AgentReport], merge_reports]
    shared_evidence: Annotated[dict[str, Any], merge_dicts]

    # SEQUENTIAL UPDATES — no reducer needed
    iteration: int
    max_iterations: int
    final_decision: CouncilDecision | None


# ============================================================
# Helpers
# ============================================================

def _get_agent_instance(state: CouncilState, key: str) -> Any:
    """
    Fetch agent instance from the external registry (NOT from state).
    State must remain serializable for LangGraph checkpointing.
    """
    from core.agent_registry import get_agent_registry
    registry = get_agent_registry()
    scan_id = state.get("scan_id", "")
    return registry.get(scan_id, key)


def _empty_report(agent: AgentName, error: str | None = None) -> AgentReport:
    return AgentReport(
        agent=agent,
        findings=[],
        scan_duration_ms=0,
        errors_encountered=[error] if error else [],
    )


# ============================================================
# Graph Nodes
# ============================================================

async def entry_node(state: CouncilState) -> dict[str, Any]:
    """Initialize the scan."""
    scan_id = state.get("scan_id") or str(uuid4())
    logger.info(f"Council scan started: {scan_id}")
    return {
        "scan_id": scan_id,
        "iteration": 0,
        "reports": {},
        "shared_evidence": state.get("shared_evidence", {}),
    }


async def resource_warden_node(state: CouncilState) -> dict[str, Any]:
    """Run Resource Warden in isolation."""
    warden = _get_agent_instance(state, "_warden_instance")
    if warden is None:
        logger.debug("Resource Warden not configured, skipping")
        return {"reports": {AgentName.RESOURCE_WARDEN.value: _empty_report(AgentName.RESOURCE_WARDEN)}}

    try:
        # Build a temporary ScanContext-like object for the agent
        from validation.schemas import ScanContext
        ctx = ScanContext(
            scan_id=state["scan_id"],
            scan_type=state.get("scan_type", "system"),
            target_path=state.get("target_path"),
            shared_evidence=state.get("shared_evidence", {}),
        )
        report = await warden.analyze_with_validation(ctx)
        return {"reports": {AgentName.RESOURCE_WARDEN.value: report}}
    except Exception as e:
        logger.exception(f"Resource Warden node failed: {e}")
        return {"reports": {AgentName.RESOURCE_WARDEN.value: _empty_report(AgentName.RESOURCE_WARDEN, str(e))}}


async def cyber_analyst_node(state: CouncilState) -> dict[str, Any]:
    """Run Cyber Analyst (placeholder until implemented)."""
    analyst = _get_agent_instance(state, "_analyst_instance")
    if analyst is None:
        logger.debug("Cyber Analyst not configured, skipping")
        return {"reports": {AgentName.CYBER_ANALYST.value: _empty_report(AgentName.CYBER_ANALYST)}}

    try:
        from validation.schemas import ScanContext
        ctx = ScanContext(
            scan_id=state["scan_id"],
            scan_type=state.get("scan_type", "system"),
            target_path=state.get("target_path"),
        )
        report = await analyst.analyze_with_validation(ctx)
        return {"reports": {AgentName.CYBER_ANALYST.value: report}}
    except Exception as e:
        logger.exception(f"Cyber Analyst node failed: {e}")
        return {"reports": {AgentName.CYBER_ANALYST.value: _empty_report(AgentName.CYBER_ANALYST, str(e))}}


async def traffic_observer_node(state: CouncilState) -> dict[str, Any]:
    """Run Traffic Observer (placeholder until implemented)."""
    observer = _get_agent_instance(state, "_observer_instance")
    if observer is None:
        logger.debug("Traffic Observer not configured, skipping")
        return {"reports": {AgentName.TRAFFIC_OBSERVER.value: _empty_report(AgentName.TRAFFIC_OBSERVER)}}

    try:
        from validation.schemas import ScanContext
        ctx = ScanContext(
            scan_id=state["scan_id"],
            scan_type=state.get("scan_type", "system"),
            target_path=state.get("target_path"),
        )
        report = await observer.analyze_with_validation(ctx)
        return {"reports": {AgentName.TRAFFIC_OBSERVER.value: report}}
    except Exception as e:
        logger.exception(f"Traffic Observer node failed: {e}")
        return {"reports": {AgentName.TRAFFIC_OBSERVER.value: _empty_report(AgentName.TRAFFIC_OBSERVER, str(e))}}


async def cross_reference_node(state: CouncilState) -> dict[str, Any]:
    """Find correlations across agents' findings."""
    reports = state.get("reports", {})

    pid_to_findings: dict[int, list[Finding]] = {}
    file_to_findings: dict[str, list[Finding]] = {}

    for report in reports.values():
        if not isinstance(report, AgentReport):
            continue  # safety: ignore malformed entries
        for finding in report.findings:
            if finding.location.pid is not None:
                pid_to_findings.setdefault(finding.location.pid, []).append(finding)
            if finding.location.file_path:
                file_to_findings.setdefault(finding.location.file_path, []).append(finding)

    correlations = {
        "multi_agent_pids": [
            pid for pid, fs in pid_to_findings.items() if len(fs) >= 2
        ],
        "multi_agent_files": [
            path for path, fs in file_to_findings.items() if len(fs) >= 2
        ],
    }

    logger.info(
        f"Cross-reference: {len(correlations['multi_agent_pids'])} multi-agent PIDs, "
        f"{len(correlations['multi_agent_files'])} multi-agent files"
    )

    return {"shared_evidence": {"correlations": correlations}}


async def arbitrator_node(state: CouncilState) -> dict[str, Any]:
    """Final deliberation."""
    arbitrator = _get_agent_instance(state, "_arbitrator_instance")
    
    # Reconstruct ScanContext for the arbitrator
    from validation.schemas import ScanContext
    ctx = ScanContext(
        scan_id=state["scan_id"],
        scan_type=state.get("scan_type", "system"),
        reports={AgentName(k): v for k, v in state.get("reports", {}).items() if isinstance(v, AgentReport)},
        shared_evidence=state.get("shared_evidence", {}),
        iteration=state.get("iteration", 0),
        max_iterations=state.get("max_iterations", 3),
    )

    if arbitrator is None:
        decision = _vote_only_decision(ctx)
    else:
        try:
            decision = await arbitrator.deliberate(ctx)
        except Exception as e:
            logger.exception(f"Arbitrator failed, using vote-only fallback: {e}")
            decision = _vote_only_decision(ctx)

    return {
        "final_decision": decision,
        "iteration": state.get("iteration", 0) + 1,
    }


def _vote_only_decision(ctx) -> CouncilDecision:
    """Fallback: produce decision from majority vote without LLM."""
    all_findings = [
        f for report in ctx.reports.values() for f in report.findings
    ]

    if not all_findings:
        return CouncilDecision(
            overall_threat_level=ThreatLevel.CLEAN,
            confidence=0.7,
            consensus_reached=True,
            voting_summary={"clean": 3},
            primary_findings=[],
            recommended_actions=[],
            user_summary_ar="لم يتم اكتشاف أي تهديدات.",
            user_summary_en="No threats detected.",
            technical_report="All agents reported no findings.",
        )

    max_level = max((f.threat_level for f in all_findings), key=lambda t: t.numeric)
    avg_confidence = sum(f.confidence for f in all_findings) / len(all_findings)

    primary = [f for f in all_findings if f.confidence >= 0.6]
    suppressed = [f for f in all_findings if f.confidence < 0.6]

    return CouncilDecision(
        overall_threat_level=max_level,
        confidence=avg_confidence,
        consensus_reached=len(primary) > 0,
        voting_summary={
            lvl.value: sum(1 for f in all_findings if f.threat_level == lvl)
            for lvl in ThreatLevel
        },
        primary_findings=primary,
        suppressed_findings=suppressed,
        recommended_actions=list({f.recommended_action for f in primary}),
        user_summary_ar=f"تم اكتشاف {len(primary)} تهديد بمستوى {max_level.value}.",
        user_summary_en=f"Detected {len(primary)} threats at level {max_level.value}.",
        technical_report="Vote-only decision (LLM Arbitrator unavailable).",
    )


# ============================================================
# Conditional routing
# ============================================================

def should_iterate(state: CouncilState) -> Literal["iterate", "finalize"]:
    """Decide if council needs another deliberation round."""
    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", 3)
    decision = state.get("final_decision")

    if iteration >= max_iter:
        return "finalize"
    if decision is None:
        return "finalize"
    if not decision.consensus_reached:
        logger.info(f"No consensus on iteration {iteration}. Re-deliberating.")
        return "iterate"
    return "finalize"


# ============================================================
# Graph builder
# ============================================================

def build_council_graph() -> Any:
    """Build and compile the council state machine."""
    workflow = StateGraph(CouncilState)

    workflow.add_node("entry", entry_node)
    workflow.add_node("resource_warden", resource_warden_node)
    workflow.add_node("cyber_analyst", cyber_analyst_node)
    workflow.add_node("traffic_observer", traffic_observer_node)
    workflow.add_node("cross_reference", cross_reference_node)
    workflow.add_node("arbitrator", arbitrator_node)

    workflow.set_entry_point("entry")

    # Parallel fan-out
    workflow.add_edge("entry", "resource_warden")
    workflow.add_edge("entry", "cyber_analyst")
    workflow.add_edge("entry", "traffic_observer")

    # Fan-in: cross_reference waits for ALL three (LangGraph handles this)
    workflow.add_edge("resource_warden", "cross_reference")
    workflow.add_edge("cyber_analyst", "cross_reference")
    workflow.add_edge("traffic_observer", "cross_reference")

    workflow.add_edge("cross_reference", "arbitrator")

    workflow.add_conditional_edges(
        "arbitrator",
        should_iterate,
        {
            "iterate": "cross_reference",
            "finalize": END,
        },
    )

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)
