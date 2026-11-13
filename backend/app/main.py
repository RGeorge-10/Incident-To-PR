from __future__ import annotations

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .database import (TERMINAL_STATUSES, add_event, count_open_incidents, create_incident, find_open_incident,
                       get_incident, init_db, list_events, list_incidents, recover_interrupted_analyses,
                       save_incident, set_app_settings)
from .integrations.argocd import application_status
from .integrations.github import GitHubClient, GitHubError
from .integrations.telemetry import integration_status, prometheus_fleet, prometheus_health, prometheus_health_window
from .schemas import (AlertmanagerPayload, EscalateRequest, IncidentCreate, MonitorConfigUpdate,
                      RepositoryConfigUpdate, ReviewRequest, SessionModelUpdate)
from .services import monitor, workflow
from .services.incident_ops import ANALYZABLE_STATUSES, start_analysis, wait_for_background_analyses
from .services.monitor import monitor_loop
from .services.policy import MAX_REPLICAS, MIN_REPLICAS, RULE_VERSION
from .services.runtime_config import auto_monitor_config, normalize_github_repository, repository_config


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    recovered = recover_interrupted_analyses()
    if recovered:
        logger.warning("Marked %s interrupted analysis run(s) as failed after restart", recovered)
    monitor_task = asyncio.create_task(monitor_loop())
    try:
        yield
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        await wait_for_background_analyses()


logger = logging.getLogger(__name__)
app = FastAPI(title="Incident-to-PR API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=sorted({settings.frontend_origin, "http://localhost:3000", "http://127.0.0.1:3000"}), allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["Content-Type", "X-Alertmanager-Token"])


def _get_or_404(incident_id: str) -> dict[str, Any]:
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")
    return incident


@app.get("/api/v1/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "incident-to-pr-api", "environment": settings.app_env,
            "simulation": settings.simulation}


@app.get("/api/v1/integrations")
async def integrations() -> dict[str, Any]:
    status, argo = await asyncio.gather(integration_status(), application_status())
    repos = repository_config()
    repositories = " · ".join(dict.fromkeys(r for r in (repos["code_repository"], repos["gitops_repository"]) if r))
    ready = bool(settings.github_token and repos["code_repository"] and repos["gitops_repository"])
    status["github"].update({"configured": ready, "reachable": ready, "detail": repositories or None,
                             "reason": None if ready else ("GITHUB_TOKEN is not set" if not settings.github_token
                                                           else "no repository connected")})
    status["argocd"].update({"configured": argo.get("configured", False),
                             "reachable": bool(argo.get("configured") and not argo.get("reason")),
                             "synced": argo.get("synced", False), "healthy": argo.get("healthy", False),
                             "sync_status": argo.get("sync_status"), "health_status": argo.get("health_status"),
                             "reason": argo.get("reason")})
    return status


@app.get("/api/v1/configuration")
async def get_configuration() -> dict[str, Any]:
    repos = repository_config()
    monitoring = auto_monitor_config()
    return {"code_repository": repos["code_repository"], "gitops_repository": repos["gitops_repository"],
            "github_token_configured": bool(settings.github_token),
            "model_configured": bool(settings.llm_model and settings.llm_api_key),
            "model": workflow.active_model()[0] or None,
            "model_source": "session" if workflow.session_model else "configuration",
            "monitoring": {**monitoring, "poll_seconds": monitor.POLL_SECONDS,
                           "consecutive_polls": monitor.CONSECUTIVE_UNHEALTHY_POLLS, "last_poll": monitor.last_poll},
            "namespace": settings.k8s_namespace, "base_branch": settings.github_base_branch,
            "simulation": settings.simulation, "environment": settings.app_env,
            "verification_window_minutes": settings.verification_window_minutes,
            "evidence_max_age_seconds": settings.evidence_max_age_seconds}


@app.put("/api/v1/configuration/model")
async def set_session_model(payload: SessionModelUpdate) -> dict[str, Any]:
    """Simulation lab only: use a real model for this process's lifetime. The key is kept in memory only."""
    if not settings.simulation:
        raise HTTPException(403, "Outside the simulation lab the model key is configured in .env, never through the browser")
    model, api_key = payload.model.strip(), payload.api_key.strip()
    try:
        await workflow.check_model(model, api_key)
    except Exception as exc:
        raise HTTPException(422, f"The provider rejected the test request ({type(exc).__name__}). Check the model id and the key.") from exc
    workflow.session_model.update({"model": model, "api_key": api_key})
    return {"model": model, "model_source": "session"}


@app.delete("/api/v1/configuration/model")
async def clear_session_model() -> dict[str, Any]:
    workflow.session_model.clear()
    return {"model": settings.llm_model or None, "model_source": "configuration"}


@app.get("/api/v1/fleet")
async def fleet() -> dict[str, Any]:
    return await prometheus_fleet()


@app.put("/api/v1/configuration/repositories")
async def configure_repositories(payload: RepositoryConfigUpdate) -> dict[str, Any]:
    try:
        code_repo = normalize_github_repository(payload.code_repository_url)
        gitops_repo = normalize_github_repository(payload.gitops_repository_url) if payload.gitops_repository_url.strip() else code_repo
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if settings.github_token:
        try:
            github = GitHubClient()
            await github.get_repository(code_repo)
            if gitops_repo != code_repo:
                await github.get_repository(gitops_repo)
        except GitHubError as exc:
            raise HTTPException(422, f"GitHub could not access the selected repository: {exc}") from exc
    set_app_settings({"code_repository": code_repo, "gitops_repository": gitops_repo})
    return {"code_repository": code_repo, "gitops_repository": gitops_repo,
            "verified": bool(settings.github_token), "github_token_configured": bool(settings.github_token)}


@app.put("/api/v1/configuration/monitoring")
async def configure_monitoring(payload: MonitorConfigUpdate) -> dict[str, Any]:
    if payload.enabled and not settings.prometheus_url:
        raise HTTPException(409, "Configure Prometheus before enabling automatic monitoring")
    if payload.enabled and payload.auto_analyze:
        if not (settings.llm_model and settings.llm_api_key):
            raise HTTPException(409, "Configure the model provider before enabling automatic analysis")
    set_app_settings({"auto_monitor_enabled": str(payload.enabled).lower(),
                      "auto_analyze_enabled": str(payload.auto_analyze).lower()})
    return auto_monitor_config()


@app.get("/api/v1/policy")
async def policy_summary() -> dict[str, Any]:
    return {"require_human_approval": settings.require_human_approval,
            "auto_apply_enabled": settings.auto_apply_enabled,
            "code_allowed_paths": settings.code_allowed_paths,
            "infra_allowed_paths": settings.infra_allowed_paths,
            "max_patch_files": settings.max_patch_files,
            "max_patch_bytes": settings.max_patch_bytes,
            "infra_context_files": settings.infra_context_files,
            "code_context_files": settings.code_context_files,
            "infra_change_scope": f"Deployment spec.replicas only, integer {MIN_REPLICAS}-{MAX_REPLICAS}",
            "evidence_max_age_seconds": settings.evidence_max_age_seconds,
            "rule_version": RULE_VERSION,
            "invariant": "The application never writes to Kubernetes and never merges pull requests."}


@app.get("/api/v1/incidents")
async def incidents(limit: int = 50) -> list[dict[str, Any]]:
    return list_incidents(max(1, min(limit, 100)))


@app.post("/api/v1/incidents", status_code=201)
async def create_incident_route(payload: IncidentCreate) -> dict[str, Any]:
    if count_open_incidents() >= settings.max_open_incidents:
        raise HTTPException(429, "Open incident limit reached; resolve or escalate existing incidents")
    incident = create_incident(payload.service, payload.summary, payload.source, payload.labels)
    return incident


@app.get("/api/v1/incidents/{incident_id}")
async def incident_detail(incident_id: str) -> dict[str, Any]:
    incident = _get_or_404(incident_id)
    incident["events"] = list_events(incident_id)
    return incident


@app.post("/api/v1/incidents/{incident_id}/analyze", status_code=202)
async def analyze(incident_id: str) -> dict[str, Any]:
    incident = _get_or_404(incident_id)
    if incident["status"] not in ANALYZABLE_STATUSES:
        raise HTTPException(409, f"Cannot analyze incident in status {incident['status']}")
    if not (settings.llm_model and settings.llm_api_key):
        raise HTTPException(409, "Model provider is not configured: set LLM_MODEL and LLM_API_KEY in .env and restart the API")
    return start_analysis(incident)


@app.post("/api/v1/incidents/{incident_id}/escalate")
async def escalate(incident_id: str, payload: EscalateRequest) -> dict[str, Any]:
    """Human hand-off: stop automation for this incident and close it as escalated."""
    incident = _get_or_404(incident_id)
    if incident["status"] in TERMINAL_STATUSES - {"policy_denied", "failed"} or incident["status"] == "analyzing":
        raise HTTPException(409, f"Cannot escalate incident in status {incident['status']}")
    incident["status"] = "escalated"
    incident["escalation"] = {"by": payload.reviewer, "comment": payload.comment, "at": _now()}
    add_event(incident_id, payload.reviewer, "incident.escalated_by_human", {"comment": payload.comment})
    return save_incident(incident)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence_age_seconds(incident: dict[str, Any]) -> float | None:
    try:
        analyzed = datetime.fromisoformat(str(incident.get("analyzed_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - analyzed).total_seconds()


@app.post("/api/v1/incidents/{incident_id}/review")
async def review(incident_id: str, payload: ReviewRequest) -> dict[str, Any]:
    incident = _get_or_404(incident_id)
    if incident["status"] != "awaiting_review" or not incident.get("policy", {}).get("allowed"):
        raise HTTPException(409, "Incident is not waiting for an eligible PR review")
    age = _evidence_age_seconds(incident)
    if payload.decision == "approve_pr" and (age is None or age > settings.evidence_max_age_seconds):
        add_event(incident_id, "policy-gate", "review.blocked_stale_evidence", {"evidence_age_seconds": age})
        raise HTTPException(409, "Evidence is stale; re-run the analysis so the diagnosis and proposal reflect the current state")
    incident["review"] = {"decision": payload.decision, "reviewer": payload.reviewer,
                           "comment": payload.comment, "at": datetime.now(timezone.utc).isoformat()}
    if payload.decision == "reject":
        incident["status"] = "rejected"
        add_event(incident_id, payload.reviewer, "proposal.rejected", {"comment": payload.comment})
    else:
        incident["status"] = "ready_for_pr"
        add_event(incident_id, payload.reviewer, "proposal.approved_for_pr", {"comment": payload.comment})
    return save_incident(incident)


@app.post("/api/v1/incidents/{incident_id}/open-pr")
async def open_pr(incident_id: str) -> dict[str, Any]:
    incident = _get_or_404(incident_id)
    if incident["status"] != "ready_for_pr":
        raise HTTPException(409, "Incident must pass policy and human review before creating a PR")
    if not incident.get("policy", {}).get("allowed"):
        raise HTTPException(403, "Policy denied this proposal")
    if (incident.get("review") or {}).get("decision") != "approve_pr":
        raise HTTPException(403, "Human approval is required")
    proposal = incident.get("proposal") or {}
    repo = proposal.get("repository")
    if not repo:
        raise HTTPException(503, "Target repository is not configured")
    try:
        gh = GitHubClient()
        pr = await gh.create_pull_request(repo, settings.github_base_branch,
            f"Incident {incident_id[:8]}: {incident['decision']['action']} for {incident['service']}",
            _pr_body(incident), proposal.get("patches", []), incident_id)
        incident["pull_request"] = pr
        if pr.get("merged"):
            incident["verification_started_at"] = pr.get("merged_at") or datetime.now(timezone.utc).isoformat()
            incident["status"] = "merged_waiting_verification"
        else:
            incident["status"] = "pr_open" if pr.get("state") == "open" else "rejected"
        add_event(incident_id, "github-integration", "pull_request.created", pr)
        return save_incident(incident)
    except GitHubError as exc:
        add_event(incident_id, "github-integration", "pull_request.failed", {"reason": str(exc)[:500]})
        raise HTTPException(502, str(exc)) from exc


def _pr_body(incident: dict[str, Any]) -> str:
    diagnosis = incident.get("diagnosis") or {}
    decision = incident.get("decision") or {}
    proposal = incident.get("proposal") or {}
    evidence = ", ".join(diagnosis.get("evidence_ids", [])) or "No evidence IDs"
    return (f"## Incident-to-PR proposal\n\n"
            f"- Incident: `{incident['id']}`\n- Service: `{incident['service']}`\n"
            f"- Decision: `{decision.get('action')}`\n- Evidence: {evidence}\n\n"
            f"### Diagnosis\n{diagnosis.get('root_cause', 'Not provided')}\n\n"
            f"### Proposal\n{proposal.get('summary', 'Review the diff and CI checks.')}\n\n"
            f"### Rollback plan\n{proposal.get('rollback_plan', 'Revert this pull request.')}\n\n"
            "This bot opens a pull request only. CI, required reviews, branch protection, and the GitOps controller govern deployment.")


@app.post("/api/v1/incidents/{incident_id}/refresh")
async def refresh_status(incident_id: str) -> dict[str, Any]:
    incident = _get_or_404(incident_id)
    pr = incident.get("pull_request")
    if not pr:
        raise HTTPException(409, "No pull request is associated with this incident")
    try:
        gh = GitHubClient()
        pr_state = await gh.get_pull_request(pr["repository"], int(pr["number"]))
        incident["pull_request"].update(pr_state)
        if incident["status"] == "verified":
            return save_incident(incident)
        if pr_state["merged"]:
            if not incident.get("verification_started_at"):
                incident["verification_started_at"] = pr_state.get("merged_at") or datetime.now(timezone.utc).isoformat()
            argo = await application_status()
            health = await prometheus_health(incident["service"])
            incident["verification"] = {"argocd": argo, "prometheus": health,
                                         "checked_at": datetime.now(timezone.utc).isoformat()}
            if argo.get("configured") and argo.get("synced") and argo.get("healthy") and health.get("configured") and health.get("healthy"):
                incident["status"] = "deployed_verifying"
                add_event(incident_id, "verifier", "deployment.healthy_initial", incident["verification"])
            else:
                incident["status"] = "merged_waiting_verification"
                add_event(incident_id, "verifier", "verification.pending", incident["verification"])
        elif pr_state.get("state") == "closed":
            incident["status"] = "rejected"
            add_event(incident_id, "verifier", "pull_request.closed_unmerged", pr_state)
        else:
            incident["status"] = "pr_open"
        return save_incident(incident)
    except GitHubError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/v1/incidents/{incident_id}/verify")
async def verify_incident(incident_id: str) -> dict[str, Any]:
    incident = _get_or_404(incident_id)
    pr = incident.get("pull_request") or {}
    if not pr.get("merged"):
        raise HTTPException(409, "Pull request must be merged through GitHub before verification")
    if incident["status"] == "verified":
        return incident  # A verified record is immutable.
    if not incident.get("verification_started_at"):
        incident["verification_started_at"] = pr.get("merged_at") or datetime.now(timezone.utc).isoformat()
    try:
        merged_at = datetime.fromisoformat(incident["verification_started_at"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        merged_at = datetime.now(timezone.utc)
    argo = await application_status()
    health = await prometheus_health(incident["service"])
    since = merged_at.astimezone(timezone.utc)
    windows = {}
    long_minutes = settings.verification_window_minutes
    short_minutes = min(5, long_minutes)
    for minutes in dict.fromkeys((short_minutes, long_minutes)):
        windows[str(minutes)] = await prometheus_health_window(incident["service"], minutes, since)
    checks = {"argocd": argo, "prometheus": health, "prometheus_windows": windows,
              "checked_at": datetime.now(timezone.utc).isoformat(), "started_at": since.isoformat()}
    incident["verification"] = checks
    if (argo.get("configured") and argo.get("synced") and argo.get("healthy") and
            all(window.get("configured") and window.get("stable") for window in windows.values())):
        incident["status"] = "verified"
        add_event(incident_id, "verifier", "incident.verified", checks)
    else:
        # Record the first failed check of each waiting period, not every poll of the button.
        if incident["status"] != "merged_waiting_verification" or not incident.get("verification_logged"):
            add_event(incident_id, "verifier", "incident.not_verified", checks)
            incident["verification_logged"] = True
        incident["status"] = "merged_waiting_verification"
    return save_incident(incident)


@app.post("/api/v1/webhooks/alertmanager", status_code=202)
async def alertmanager_webhook(payload: AlertmanagerPayload, x_alertmanager_token: str = Header(default="")) -> dict[str, Any]:
    if not settings.alertmanager_webhook_token or not hmac.compare_digest(
            x_alertmanager_token.encode("utf-8"), settings.alertmanager_webhook_token.encode("utf-8")):
        raise HTTPException(401, "Invalid webhook token")
    accepted, skipped = [], []
    for alert in payload.alerts[:50]:
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
        service = str(labels.get("deployment") or labels.get("service") or labels.get("app") or labels.get("job") or "unknown-service")[:200]
        existing = find_open_incident(service, "alertmanager")
        if str(alert.get("status") or payload.status).lower() == "resolved":
            if existing:
                add_event(existing["id"], "alertmanager", "alert.resolved", {"alertname": labels.get("alertname")})
            skipped.append({"service": service, "reason": "resolved"})
            continue
        if existing:  # Alertmanager re-sends firing alerts; never open a duplicate for the same service.
            add_event(existing["id"], "alertmanager", "alert.repeated", {"alertname": labels.get("alertname")})
            skipped.append({"service": service, "reason": "duplicate", "id": existing["id"]})
            continue
        if count_open_incidents() >= settings.max_open_incidents:
            skipped.append({"service": service, "reason": "open incident limit reached"})
            continue
        summary = annotations.get("summary") or annotations.get("description") or labels.get("alertname") or "Alertmanager alert"
        incident = create_incident(service, str(summary)[:2000], "alertmanager", labels)
        accepted.append({"id": incident["id"], "service": service})
    return {"accepted": accepted, "skipped": skipped}


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": f"Request failed safely ({type(exc).__name__})"})
