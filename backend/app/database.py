from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with _connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, service TEXT NOT NULL,
            summary TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
            actor TEXT NOT NULL, event_type TEXT NOT NULL, details TEXT NOT NULL,
            FOREIGN KEY(incident_id) REFERENCES incidents(id)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_incidents_updated ON incidents(updated_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_events_incident ON audit_events(incident_id, occurred_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS monitor_state (
            service TEXT PRIMARY KEY, unhealthy_polls INTEGER NOT NULL DEFAULT 0,
            active_incident_id TEXT, recovery_noted INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )""")


def get_app_setting(key: str, default: str = "") -> str:
    with _connect() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_app_settings(values: dict[str, str]) -> None:
    timestamp = now_iso()
    with _connect() as db:
        db.executemany("INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                       [(key, value, timestamp) for key, value in values.items()])


def get_monitor_state(service: str) -> dict[str, Any]:
    with _connect() as db:
        row = db.execute("SELECT * FROM monitor_state WHERE service=?", (service,)).fetchone()
    return dict(row) if row else {"service": service, "unhealthy_polls": 0, "active_incident_id": None,
                                  "recovery_noted": 0, "updated_at": now_iso()}


def list_monitor_states() -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM monitor_state").fetchall()
    return [dict(row) for row in rows]


def save_monitor_state(state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    with _connect() as db:
        db.execute("""INSERT INTO monitor_state(service,unhealthy_polls,active_incident_id,recovery_noted,updated_at)
            VALUES(:service,:unhealthy_polls,:active_incident_id,:recovery_noted,:updated_at)
            ON CONFLICT(service) DO UPDATE SET unhealthy_polls=excluded.unhealthy_polls,
            active_incident_id=excluded.active_incident_id,recovery_noted=excluded.recovery_noted,
            updated_at=excluded.updated_at""", state)


def create_incident(service: str, summary: str, source: str, labels: dict[str, Any] | None = None) -> dict[str, Any]:
    incident_id = str(uuid4())
    timestamp = now_iso()
    incident = {
        "id": incident_id, "service": service, "summary": summary, "source": source,
        "labels": labels or {}, "status": "open", "created_at": timestamp,
        "updated_at": timestamp, "evidence": [], "diagnosis": None, "decision": None,
        "proposal": None, "policy": None, "review": None, "pull_request": None,
        "verification": None, "failure": None,
    }
    with _connect() as db:
        db.execute("INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (incident_id, incident["status"], service, summary, timestamp, timestamp, json.dumps(incident)))
    add_event(incident_id, "system", "incident.created", {"service": service, "source": source})
    return incident


def get_incident(incident_id: str) -> dict[str, Any] | None:
    with _connect() as db:
        row = db.execute("SELECT payload FROM incidents WHERE id=?", (incident_id,)).fetchone()
    return json.loads(row["payload"]) if row else None


def list_incidents(limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT payload FROM incidents ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def save_incident(incident: dict[str, Any]) -> dict[str, Any]:
    incident["updated_at"] = now_iso()
    with _connect() as db:
        db.execute("UPDATE incidents SET status=?, updated_at=?, payload=? WHERE id=?",
                   (incident["status"], incident["updated_at"], json.dumps(incident), incident["id"]))
    return incident


def add_event(incident_id: str, actor: str, event_type: str, details: dict[str, Any]) -> dict[str, Any]:
    event = {"id": str(uuid4()), "incident_id": incident_id, "occurred_at": now_iso(),
             "actor": actor, "event_type": event_type, "details": details}
    with _connect() as db:
        db.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
                   (event["id"], incident_id, event["occurred_at"], actor, event_type, json.dumps(details)))
    return event


def list_events(incident_id: str) -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM audit_events WHERE incident_id=? ORDER BY occurred_at", (incident_id,)).fetchall()
    return [{"id": r["id"], "incident_id": r["incident_id"], "occurred_at": r["occurred_at"],
             "actor": r["actor"], "event_type": r["event_type"], "details": json.loads(r["details"])} for r in rows]


# Statuses in which an incident needs no further automated or human workflow step.
TERMINAL_STATUSES = frozenset({"verified", "denied", "rejected", "failed", "escalated", "policy_denied"})


def count_open_incidents() -> int:
    marks = ",".join("?" for _ in TERMINAL_STATUSES)
    with _connect() as db:
        row = db.execute(f"SELECT COUNT(*) AS n FROM incidents WHERE status NOT IN ({marks})",
                         tuple(TERMINAL_STATUSES)).fetchone()
    return int(row["n"])


def find_open_incident(service: str, source: str | None = None) -> dict[str, Any] | None:
    """Return the newest non-terminal incident for a service (optionally from one source)."""
    marks = ",".join("?" for _ in TERMINAL_STATUSES)
    with _connect() as db:
        rows = db.execute(f"SELECT payload FROM incidents WHERE service=? AND status NOT IN ({marks}) "
                          "ORDER BY created_at DESC", (service, *TERMINAL_STATUSES)).fetchall()
    for row in rows:
        incident = json.loads(row["payload"])
        if source is None or incident.get("source") == source:
            return incident
    return None


def recover_interrupted_analyses() -> int:
    """An analysis cannot survive a process restart; fail those incidents closed so they can be retried."""
    with _connect() as db:
        rows = db.execute("SELECT payload FROM incidents WHERE status='analyzing'").fetchall()
    for row in rows:
        incident = json.loads(row["payload"])
        incident["status"] = "failed"
        incident["failure"] = "Analysis was interrupted by an API restart. No change was proposed; run the analysis again."
        save_incident(incident)
        add_event(incident["id"], "system", "analysis.interrupted", {"reason": "api_restart"})
    return len(rows)
