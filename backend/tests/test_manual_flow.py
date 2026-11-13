"""Manual mode, end to end, through the real API/LangGraph/LiteLLM/GitHub-client code paths."""
from __future__ import annotations

import yaml

from simulator.main import world

from .conftest import age_world, analyze, backdate_merge, inject_fault, sim_post

REVIEW = {"decision": "approve_pr", "reviewer": "dheeran", "comment": "looks right"}


def test_infrastructure_fault_runs_from_report_to_verified_recovery(api):
    inject_fault("shippingservice", "capacity")
    incident = analyze(api, "shippingservice", "Checkout is failing; shipping quotes time out")
    iid = incident["id"]

    # Evidence, diagnosis, decision
    sources = {e["source"]: e for e in incident["evidence"]}
    assert set(sources) == {"prometheus", "loki", "kubernetes", "incident_report"}
    assert not sources["prometheus"].get("error") and not sources["loki"].get("error")
    assert any("OOMKilled" in item["line"] for item in sources["loki"]["items"])
    assert sources["kubernetes"]["error"]  # no cluster in tests: reported, not hidden
    assert incident["diagnosis"]["fault_class"] == "INFRA"
    assert set(incident["diagnosis"]["evidence_ids"]) <= {e["id"] for e in incident["evidence"]}
    assert incident["decision"]["action"] == "INFRA_FIX"

    # Proposal + policy
    assert incident["status"] == "awaiting_review"
    assert incident["policy"]["allowed"] and incident["policy"]["requires_approval"]
    proposal = incident["proposal"]
    assert proposal["repository"] == "sim-lab/online-boutique"
    assert proposal["summary"] and proposal["rollback_plan"]
    [patch] = proposal["patches"]
    assert patch["path"] == "deploy/online-boutique/shippingservice.yaml"
    assert yaml.safe_load(patch["content"])["spec"]["replicas"] == 3

    # No PR without human approval
    assert api.post(f"/api/v1/incidents/{iid}/open-pr").status_code == 409
    assert world.pulls == []
    assert api.post(f"/api/v1/incidents/{iid}/review", json=REVIEW).json()["status"] == "ready_for_pr"

    opened = api.post(f"/api/v1/incidents/{iid}/open-pr").json()
    assert opened["status"] == "pr_open"
    assert opened["pull_request"]["number"] == 1 and opened["pull_request"]["head_branch"] == f"incident-to-pr/{iid[:8]}"
    # The bot pushed a branch and opened a PR; main is untouched and nothing is merged.
    assert yaml.safe_load(world.branches["main"]["files"][patch["path"]])["spec"]["replicas"] == 1
    assert world.pulls[0]["state"] == "open" and world.pulls[0]["merged_at"] is None

    # Verification is impossible before a human merges on GitHub.
    assert api.post(f"/api/v1/incidents/{iid}/verify").status_code == 409
    assert api.post(f"/api/v1/incidents/{iid}/refresh").json()["status"] == "pr_open"

    sim_post("/sim/pulls/1/merge")  # the human reviewer's action, outside the control plane
    refreshed = api.post(f"/api/v1/incidents/{iid}/refresh").json()
    assert refreshed["pull_request"]["merged"] is True
    assert refreshed["status"] == "deployed_verifying"
    assert refreshed["verification"]["argocd"]["synced"] and refreshed["verification"]["prometheus"]["healthy"]

    # Recovery only counts after the full observation window.
    early = api.post(f"/api/v1/incidents/{iid}/verify").json()
    assert early["status"] == "merged_waiting_verification"
    assert not early["verification"]["prometheus_windows"]["2"]["stable"]

    age_world(600)
    backdate_merge(iid, 300)
    verified = api.post(f"/api/v1/incidents/{iid}/verify").json()
    assert verified["status"] == "verified"
    window = verified["verification"]["prometheus_windows"]["2"]
    assert window["stable"] and window["samples"] == window["healthy_samples"] >= 3

    # A verified record is immutable.
    again = api.post(f"/api/v1/incidents/{iid}/verify").json()
    assert again["verification"]["checked_at"] == verified["verification"]["checked_at"]

    events = [e["event_type"] for e in api.get(f"/api/v1/incidents/{iid}").json()["events"]]
    for expected in ("incident.created", "analysis.started", "evidence.collected", "llm.request", "llm.response",
                     "diagnosis.completed", "decision.made", "proposal.generated", "policy.evaluated",
                     "analysis.completed", "proposal.approved_for_pr", "pull_request.created", "incident.verified"):
        assert expected in events, expected
    assert events.count("llm.request") == 3  # diagnoser, decider, engineer


def test_code_fault_produces_a_source_patch_in_the_service_directory(api):
    inject_fault("cartservice", "code_bug")
    incident = analyze(api, "cartservice", "Cart page returns 500s")
    assert incident["diagnosis"]["fault_class"] == "CODE"
    assert incident["decision"]["action"] == "CODE_FIX"
    assert incident["status"] == "awaiting_review"
    [patch] = incident["proposal"]["patches"]
    assert patch["path"] == "src/cartservice/main.go"
    assert "len(items) == 0" in patch["content"] and "len(items) == 0" not in patch["base_content"]


def test_unhealthy_sample_inside_the_window_blocks_verification(api):
    inject_fault("shippingservice", "capacity")
    iid = analyze(api, "shippingservice")["id"]
    api.post(f"/api/v1/incidents/{iid}/review", json=REVIEW)
    api.post(f"/api/v1/incidents/{iid}/open-pr")
    sim_post("/sim/pulls/1/merge")
    api.post(f"/api/v1/incidents/{iid}/refresh")
    import time
    now = time.time()
    with world.lock:  # outage lasted from 150 s ago until 60 s ago: inside the two-minute window
        world.history["shippingservice"] = [(now - 86400, 1), (now - 150, 0), (now - 60, 3)]
    backdate_merge(iid, 300)
    result = api.post(f"/api/v1/incidents/{iid}/verify").json()
    assert result["status"] == "merged_waiting_verification"
    window = result["verification"]["prometheus_windows"]["2"]
    assert window["healthy_samples"] < window["samples"]


def test_infra_patch_that_masks_a_code_fault_is_not_verified(api):
    """A replica bump merged for a code bug: Argo syncs, but the service never becomes healthy."""
    inject_fault("shippingservice", "capacity")
    iid = analyze(api, "shippingservice")["id"]
    with world.lock:
        world.faults["shippingservice"]["kind"] = "code_bug"  # the real cause differs from the diagnosis
    api.post(f"/api/v1/incidents/{iid}/review", json=REVIEW)
    api.post(f"/api/v1/incidents/{iid}/open-pr")
    sim_post("/sim/pulls/1/merge")
    refreshed = api.post(f"/api/v1/incidents/{iid}/refresh").json()
    assert refreshed["status"] == "merged_waiting_verification"
    age_world(600)
    backdate_merge(iid, 300)
    assert api.post(f"/api/v1/incidents/{iid}/verify").json()["status"] == "merged_waiting_verification"


def test_ambiguous_fault_escalates_without_a_patch(api):
    inject_fault("paymentservice", "ambiguous")
    incident = analyze(api, "paymentservice", "Payments intermittently fail")
    assert incident["status"] == "escalated"
    assert incident["decision"]["action"] == "ESCALATE_HUMAN"
    assert incident["proposal"] is None and incident["policy"]["allowed"] is False
    assert api.post(f"/api/v1/incidents/{incident['id']}/review", json=REVIEW).status_code == 409
    assert api.post(f"/api/v1/incidents/{incident['id']}/open-pr").status_code == 409


def test_report_without_supporting_telemetry_escalates(api):
    incident = analyze(api, "frontend", "I think the site feels slow")
    assert incident["diagnosis"]["fault_class"] == "UNKNOWN"
    assert incident["status"] == "escalated"


def test_rejecting_a_proposal_ends_the_path(api):
    inject_fault("shippingservice", "capacity")
    iid = analyze(api, "shippingservice")["id"]
    rejected = api.post(f"/api/v1/incidents/{iid}/review", json={"decision": "reject", "reviewer": "dheeran"}).json()
    assert rejected["status"] == "rejected"
    assert api.post(f"/api/v1/incidents/{iid}/open-pr").status_code == 409
    assert world.pulls == []


def test_pr_closed_without_merge_marks_the_incident_rejected(api):
    inject_fault("shippingservice", "capacity")
    iid = analyze(api, "shippingservice")["id"]
    api.post(f"/api/v1/incidents/{iid}/review", json=REVIEW)
    api.post(f"/api/v1/incidents/{iid}/open-pr")
    sim_post("/sim/pulls/1/close")
    assert api.post(f"/api/v1/incidents/{iid}/refresh").json()["status"] == "rejected"


def test_opening_the_pr_twice_reuses_the_same_pull_request(api):
    inject_fault("shippingservice", "capacity")
    iid = analyze(api, "shippingservice")["id"]
    api.post(f"/api/v1/incidents/{iid}/review", json=REVIEW)
    first = api.post(f"/api/v1/incidents/{iid}/open-pr").json()
    from app import database
    incident = database.get_incident(iid)
    incident["status"] = "ready_for_pr"  # simulate a client retry after a lost response
    database.save_incident(incident)
    second = api.post(f"/api/v1/incidents/{iid}/open-pr").json()
    assert second["pull_request"]["number"] == first["pull_request"]["number"] == 1
    assert len(world.pulls) == 1
