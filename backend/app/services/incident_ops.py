from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import settings
from ..database import add_event, get_incident, now_iso, save_incident
from .policy import RULE_VERSION
from .runtime_config import repository_config
from .workflow import ModelError, WorkflowUnavailable, analyze_incident

logger = logging.getLogger(__name__)
ANALYZABLE_STATUSES = {"open", "failed", "policy_denied", "awaiting_review"}
_tasks: set[asyncio.Task[Any]] = set()


def begin_analysis(incident: dict[str, Any]) -> dict[str, Any]:
    """Mark the incident as analyzing (synchronously, so a second request cannot race) and reset stale results."""
    incident.update({"status": "analyzing", "failure": None, "review": None, "analysis_started_at": now_iso()})
    save_incident(incident)
    add_event(incident["id"], "workflow", "analysis.started", {})
    return incident


def start_analysis(incident: dict[str, Any]) -> dict[str, Any]:
    """Run the analysis in the background; callers poll the incident for the outcome."""
    incident = begin_analysis(incident)
    task = asyncio.create_task(_execute(incident))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return incident


async def run_analysis(incident: dict[str, Any]) -> dict[str, Any]:
    return await _execute(begin_analysis(incident))


async def wait_for_background_analyses() -> None:
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _execute(incident: dict[str, Any]) -> dict[str, Any]:
    incident_id = incident["id"]
    try:
        result = await analyze_incident(incident)
        # Re-read: the row is the source of truth and may have gained fields while the model was running.
        incident = get_incident(incident_id) or incident
        decision = result.get("decision") or {"action": "ESCALATE_HUMAN", "rationale": "No decision was produced"}
        action = decision.get("action")
        repos = repository_config()
        incident["evidence"] = result.get("evidence", [])
        incident["diagnosis"] = result.get("diagnosis")
        incident["decision"] = decision
        incident["analyzed_at"] = now_iso()
        policy = result.get("policy") or {"allowed": False, "requires_approval": True, "reasons": [],
                                          "checks": [], "rule_version": RULE_VERSION}
        if result.get("workflow_error"):
            policy.setdefault("reasons", []).append(result["workflow_error"])
        incident["policy"] = policy
        if action in {"INFRA_FIX", "CODE_FIX"}:
            incident["proposal"] = {
                "patches": result.get("patches", []), "summary": result.get("engineer_summary", ""),
                "rollback_plan": result.get("rollback_plan", ""),
                "repository": result.get("repository") or (repos["gitops_repository"] if action == "INFRA_FIX" else repos["code_repository"]),
                "base_branch": settings.github_base_branch}
        else:
            incident["proposal"] = None
        if action in {"ESCALATE_HUMAN", "ROLLBACK"}:
            incident["status"] = "escalated"
        elif policy.get("allowed"):
            # Human approval is unconditional in this phase, whatever the policy result says.
            incident["status"] = "awaiting_review"
        else:
            incident["status"] = "policy_denied"
        add_event(incident_id, "workflow", "analysis.completed", {
            "decision": action, "status": incident["status"], "policy_allowed": bool(policy.get("allowed")),
            "evidence_count": len(incident["evidence"])})
    except WorkflowUnavailable as exc:
        incident = get_incident(incident_id) or incident
        incident["status"] = "open"
        incident["failure"] = str(exc)
        add_event(incident_id, "workflow", "analysis.unavailable", {"reason": str(exc)})
    except ModelError as exc:
        incident = get_incident(incident_id) or incident
        incident["status"] = "failed"
        incident["failure"] = str(exc)
        add_event(incident_id, "workflow", "analysis.failed", {"reason": str(exc)})
    except Exception as exc:
        logger.exception("Analysis for incident %s stopped", incident_id)
        incident = get_incident(incident_id) or incident
        incident["status"] = "failed"
        incident["failure"] = f"Analysis stopped safely: {type(exc).__name__}"
        add_event(incident_id, "workflow", "analysis.failed", {"reason": type(exc).__name__})
    return save_incident(incident)
