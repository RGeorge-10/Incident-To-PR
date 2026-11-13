"""Automatic detection: debounce, deduplication, recovery notes, and fail-safe polling."""
from __future__ import annotations

import asyncio

from app import database
from app.integrations import telemetry
from app.services import monitor
from app.services.incident_ops import wait_for_background_analyses
from simulator.main import world

from .conftest import inject_fault, sim_post, wait_for


def poll(times: int = 1) -> None:
    async def run():
        for _ in range(times):
            await monitor._monitor_once()
        await wait_for_background_analyses()
    asyncio.run(run())


def enable(api, auto_analyze: bool = False) -> None:
    response = api.put("/api/v1/configuration/monitoring", json={"enabled": True, "auto_analyze": auto_analyze})
    assert response.status_code == 200 and response.json() == {"enabled": True, "auto_analyze": auto_analyze}


def test_monitoring_is_off_by_default_and_creates_nothing(api):
    inject_fault("shippingservice", "capacity")
    poll(3)
    assert api.get("/api/v1/incidents").json() == []
    assert api.get("/api/v1/configuration").json()["monitoring"]["enabled"] is False


def test_healthy_fleet_creates_no_incidents(api):
    enable(api)
    poll(3)
    assert api.get("/api/v1/incidents").json() == []
    last = api.get("/api/v1/configuration").json()["monitoring"]["last_poll"]
    assert last["ok"] is True and last["unhealthy"] == [] and last["watched"] == 12


def test_two_consecutive_unhealthy_polls_create_exactly_one_incident(api):
    enable(api)
    inject_fault("shippingservice", "capacity")
    poll()
    assert api.get("/api/v1/incidents").json() == []  # one reading is not enough (debounce)
    poll()
    [incident] = api.get("/api/v1/incidents").json()
    assert incident["source"] == "prometheus_auto" and incident["service"] == "shippingservice"
    assert incident["status"] == "open"  # detection alone never starts analysis
    assert set(incident["labels"]["signals"]) == {"deployment_replica_shortfall", "crash_loop", "repeated_container_restarts"}
    assert incident["labels"]["missing_replicas"] == 1
    poll(5)
    assert len(api.get("/api/v1/incidents").json()) == 1  # deduplicated while the fault persists
    events = [e["event_type"] for e in api.get(f"/api/v1/incidents/{incident['id']}").json()["events"]]
    assert events.count("health.anomaly_confirmed") == 1


def test_a_single_transient_blip_is_debounced(api):
    enable(api)
    inject_fault("shippingservice", "capacity")
    poll()
    sim_post("/sim/reset")
    poll()
    inject_fault("shippingservice", "capacity")
    poll()
    assert api.get("/api/v1/incidents").json() == []


def test_each_unhealthy_service_gets_its_own_incident(api):
    enable(api)
    inject_fault("shippingservice", "capacity")
    inject_fault("cartservice", "code_bug")
    poll(2)
    assert sorted(i["service"] for i in api.get("/api/v1/incidents").json()) == ["cartservice", "shippingservice"]


def test_recovery_is_noted_once_and_a_new_fault_after_closure_opens_a_new_incident(api):
    enable(api)
    inject_fault("shippingservice", "capacity")
    poll(2)
    [incident] = api.get("/api/v1/incidents").json()
    with world.lock:
        world.faults.clear()
    poll(3)
    detail = api.get(f"/api/v1/incidents/{incident['id']}").json()
    assert detail["status"] == "open"  # recovery never auto-closes an incident
    assert [e["event_type"] for e in detail["events"]].count("health.returned_to_baseline") == 1

    inject_fault("shippingservice", "capacity")
    poll(3)
    assert len(api.get("/api/v1/incidents").json()) == 1  # still the same open incident

    api.post(f"/api/v1/incidents/{incident['id']}/escalate", json={"reviewer": "dheeran"})
    with world.lock:
        world.faults.clear()
    poll()
    inject_fault("shippingservice", "capacity")
    poll(2)
    assert len(api.get("/api/v1/incidents").json()) == 2


def test_automatic_analysis_stops_at_human_review(api):
    enable(api, auto_analyze=True)
    inject_fault("shippingservice", "capacity")
    poll(2)
    [incident] = api.get("/api/v1/incidents").json()
    incident = wait_for(api, incident["id"])
    assert incident["status"] == "awaiting_review" and incident["decision"]["action"] == "INFRA_FIX"
    assert world.pulls == []  # no PR is ever opened without a human approval
    events = [e["event_type"] for e in api.get(f"/api/v1/incidents/{incident['id']}").json()["events"]]
    assert "analysis.triggered_automatically" in events


def test_prometheus_outage_neither_creates_incidents_nor_resets_the_debounce(api, override):
    enable(api)
    inject_fault("shippingservice", "capacity")
    poll()
    good = telemetry.settings.prometheus_url
    override(prometheus_url="http://127.0.0.1:9")
    poll(2)
    assert api.get("/api/v1/incidents").json() == []
    assert api.get("/api/v1/configuration").json()["monitoring"]["last_poll"]["ok"] is False
    assert database.get_monitor_state("shippingservice")["unhealthy_polls"] == 1
    override(prometheus_url=good)
    poll()
    assert len(api.get("/api/v1/incidents").json()) == 1


def test_open_incident_limit_applies_to_automatic_incidents(api, override):
    override(max_open_incidents=1)
    api.post("/api/v1/incidents", json={"service": "frontend", "summary": "manual"})
    enable(api)
    inject_fault("shippingservice", "capacity")
    poll(3)
    assert len(api.get("/api/v1/incidents").json()) == 1


def test_fleet_overview_reports_replica_health(api):
    inject_fault("cartservice", "code_bug")
    fleet = {d["name"]: d for d in api.get("/api/v1/fleet").json()["deployments"]}
    assert len(fleet) == 12
    assert fleet["cartservice"] == {"name": "cartservice", "desired": 1.0, "available": 0.0, "healthy": False}
    assert fleet["frontend"]["healthy"] is True


def test_integration_status_distinguishes_configured_from_reachable(api, override):
    status = api.get("/api/v1/integrations").json()
    assert status["prometheus"]["reachable"] and status["loki"]["reachable"] and status["model"]["configured"]
    assert status["argocd"]["configured"] and status["argocd"]["synced"] and status["argocd"]["healthy"]
    assert status["kubernetes"]["reachable"] is False  # no cluster in the test environment
    telemetry._probe_cache.clear()
    override(prometheus_url="http://127.0.0.1:9")
    status = api.get("/api/v1/integrations").json()
    assert status["prometheus"]["configured"] is True and status["prometheus"]["reachable"] is False
    telemetry._probe_cache.clear()
