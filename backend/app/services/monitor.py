from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..database import (TERMINAL_STATUSES, add_event, count_open_incidents, create_incident, get_incident,
                        get_monitor_state, list_monitor_states, now_iso, save_monitor_state)
from ..integrations.telemetry import prometheus_unhealthy_deployments
from .incident_ops import start_analysis
from .runtime_config import auto_monitor_config

logger = logging.getLogger(__name__)
POLL_SECONDS = settings.monitor_poll_seconds
CONSECUTIVE_UNHEALTHY_POLLS = 2
TERMINAL_STATES = TERMINAL_STATUSES
# In-memory view of the most recent poll, exposed read-only to the dashboard.
last_poll: dict[str, object] = {"at": None, "ok": None, "error": None, "unhealthy": [], "watched": 0}


async def _monitor_once() -> None:
    options = auto_monitor_config()
    if not options["enabled"]:
        last_poll.update({"at": None, "ok": None, "error": None, "unhealthy": [], "watched": 0})
        return
    result = await prometheus_unhealthy_deployments()
    if not result.get("configured") or result.get("error"):
        reason = result.get("error", "Prometheus is unavailable")
        logger.warning("Automatic monitoring skipped: %s", reason)
        # A failed poll is not a healthy poll: counters are left untouched so an outage cannot hide a fault.
        last_poll.update({"at": now_iso(), "ok": False, "error": reason})
        return
    last_poll.update({"at": now_iso(), "ok": True, "error": None, "watched": len(result.get("services", [])),
                      "unhealthy": [{"service": i["service"], "signals": i.get("signals", [])} for i in result.get("items", [])]})
    unhealthy = {item["service"]: item for item in result.get("items", [])}
    known = {state["service"] for state in list_monitor_states()}
    for service in sorted(known | set(unhealthy)):
        state = get_monitor_state(service)
        incident_id = state.get("active_incident_id")
        incident = get_incident(incident_id) if incident_id else None
        if service in unhealthy:
            state["recovery_noted"] = 0
            state["unhealthy_polls"] += 1
            if incident is None and state["unhealthy_polls"] >= CONSECUTIVE_UNHEALTHY_POLLS:
                if count_open_incidents() >= settings.max_open_incidents:
                    logger.warning("Automatic incident skipped for %s: open incident limit reached", service)
                    save_monitor_state(state)
                    continue
                item = unhealthy[service]
                signal_summary = ", ".join(signal.replace("_", " ") for signal in item.get("signals", []))
                incident = create_incident(service,
                    f"Automatic monitor detected {signal_summary} for deployment {service} in namespace {item['namespace']}. "
                    f"Unavailable replicas: {item['missing_replicas']:g}.",
                    "prometheus_auto", {"detector": "prometheus_kubernetes_health",
                                        "namespace": item["namespace"],
                                        "missing_replicas": item["missing_replicas"],
                                        "signals": item.get("signals", []),
                                        "affected_pods": item.get("affected_pods", []),
                                        "queries": result.get("queries", {})})
                incident_id = incident["id"]
                state["active_incident_id"] = incident_id
                add_event(incident_id, "automatic-monitor", "health.anomaly_confirmed", {
                    "consecutive_polls": state["unhealthy_polls"], "poll_interval_seconds": POLL_SECONDS,
                    "missing_replicas": item["missing_replicas"]})
                if options["auto_analyze"]:
                    add_event(incident_id, "automatic-monitor", "analysis.triggered_automatically", {
                        "human_approval_still_required": True})
                    start_analysis(incident)
            elif incident and incident.get("status") in TERMINAL_STATES:
                # Keep the same incident attached while the fault persists; never create an alert loop.
                pass
        else:
            state["unhealthy_polls"] = 0
            if incident and incident.get("status") not in TERMINAL_STATES and not state.get("recovery_noted"):
                add_event(incident_id, "automatic-monitor", "health.returned_to_baseline", {
                    "note": "The signal recovered. The incident remains open for human review."})
                state["recovery_noted"] = 1
            elif incident and incident.get("status") in TERMINAL_STATES:
                state["active_incident_id"] = None
                state["recovery_noted"] = 0
        save_monitor_state(state)


async def monitor_loop() -> None:
    while True:
        try:
            await _monitor_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Automatic incident monitor cycle failed safely")
        await asyncio.sleep(POLL_SECONDS)
