"""Test harness: the real control plane wired to the in-process simulation lab.

Environment variables are set before `app` is imported because settings are read at import time.
"""
from __future__ import annotations

import os
import socket
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


SIM_PORT = _free_port()
SIM = f"http://127.0.0.1:{SIM_PORT}"
_tmp = Path(tempfile.mkdtemp(prefix="incident-to-pr-tests-"))
os.environ.update({
    "APP_ENV": "simulation", "DATABASE_PATH": str(_tmp / "test.sqlite3"),
    "PROMETHEUS_URL": f"{SIM}/prom", "LOKI_URL": f"{SIM}/loki",
    "PROMETHEUS_QUERY": "up",
    "PROMETHEUS_HEALTH_QUERY": 'kube_deployment_status_replicas_available{namespace="online-boutique",deployment="$service"}',
    "LOKI_QUERY": '{namespace="online-boutique",pod=~"$service-.*"}',
    "GITHUB_API_URL": f"{SIM}/github", "GITHUB_TOKEN": "sim-token",
    "GITHUB_CODE_REPOSITORY": "sim-lab/online-boutique", "GITHUB_GITOPS_REPOSITORY": "sim-lab/online-boutique",
    "GITHUB_BASE_BRANCH": "main",
    "CODE_ALLOWED_PATHS": "src/$service/", "INFRA_ALLOWED_PATHS": "deploy/online-boutique/",
    "CODE_CONTEXT_FILES": "src/$service/main.go", "INFRA_CONTEXT_FILES": "deploy/online-boutique/$service.yaml",
    "SERVICE_CONTEXT_MAP": str(_tmp / "no-map.json"),
    "LLM_MODEL": "openai/sim-stub", "LLM_API_KEY": "sim-key", "LLM_API_BASE": f"{SIM}/llm/v1",
    "LLM_FALLBACK_MODEL": "", "LLM_TRACE_ENABLED": "true",
    "ARGOCD_URL": f"{SIM}/argocd", "ARGOCD_TOKEN": "sim-token", "ARGOCD_APPLICATION": "online-boutique",
    "K8S_CONTEXT": "does-not-exist", "KUBECONFIG": str(_tmp / "no-kubeconfig"),
    "VERIFICATION_WINDOW_MINUTES": "2", "VERIFICATION_MINIMUM_SAMPLES": "3", "MONITOR_POLL_SECONDS": "3600",
    "EVIDENCE_MAX_AGE_SECONDS": "1800", "MAX_OPEN_INCIDENTS": "20", "ALERTMANAGER_WEBHOOK_TOKEN": "test-webhook-token",
    "SIM_SYNC_DELAY_SECONDS": "0", "SIM_PUBLIC_URL": "/sim", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
})

from fastapi.testclient import TestClient  # noqa: E402

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from simulator.main import app as sim_app, world  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def simulator():
    server = uvicorn.Server(uvicorn.Config(sim_app, host="127.0.0.1", port=SIM_PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            if httpx.get(f"{SIM}/sim/state", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        raise RuntimeError("simulator did not start")
    yield world
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def api(simulator):
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def clean_state(simulator):
    world.reset()
    database.init_db()
    with sqlite3.connect(settings.database_path) as db:
        for table in ("audit_events", "incidents", "app_settings", "monitor_state"):
            db.execute(f"DELETE FROM {table}")
    yield


@pytest.fixture
def override():
    """Temporarily change values on the frozen settings object."""
    restore: list[tuple[str, object]] = []

    def _set(**values):
        for key, value in values.items():
            restore.append((key, getattr(settings, key)))
            object.__setattr__(settings, key, value)

    yield _set
    for key, original in reversed(restore):
        object.__setattr__(settings, key, original)


def wait_for(client, incident_id: str, timeout: float = 30.0) -> dict:
    """Poll until the background analysis leaves the `analyzing` state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        incident = client.get(f"/api/v1/incidents/{incident_id}").json()
        if incident["status"] != "analyzing":
            return incident
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def analyze(client, service: str, summary: str = "Service is failing") -> dict:
    created = client.post("/api/v1/incidents", json={"service": service, "summary": summary})
    assert created.status_code == 201, created.text
    started = client.post(f"/api/v1/incidents/{created.json()['id']}/analyze")
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "analyzing"
    return wait_for(client, created.json()["id"])


def age_world(seconds: float) -> None:
    """Shift the simulator's availability history into the past (stands in for waiting)."""
    with world.lock:
        for service, points in world.history.items():
            world.history[service] = [(ts - seconds, value) for ts, value in points]


def backdate_merge(incident_id: str, seconds: float) -> None:
    incident = database.get_incident(incident_id)
    incident["verification_started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    database.save_incident(incident)


def sim_post(path: str, json: dict | None = None) -> httpx.Response:
    """Act on the simulated outside world (inject a fault, merge a PR as the human reviewer)."""
    response = httpx.post(f"{SIM}{path}", json=json, timeout=5)
    response.raise_for_status()
    return response


def inject_fault(service: str, kind: str, age_seconds: float = 120) -> None:
    sim_post("/sim/faults", {"service": service, "kind": kind})
    with world.lock:
        world.faults[service]["started"] = time.time() - age_seconds
