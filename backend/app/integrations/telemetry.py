from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import asyncio
import re
import time

import httpx
from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

from ..config import settings


INFRASTRUCTURE_PREFIXES = ("incident-to-pr", "prometheus", "grafana", "loki", "alloy")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _label(value: str) -> str:
    """Make an untrusted value safe to embed inside a PromQL/LogQL label matcher."""
    return re.sub(r"[^A-Za-z0-9_.:-]", "", value)[:200]


def _render(template: str, service: str) -> str:
    return template.replace("$service", _label(service))


async def prometheus_evidence(service: str) -> dict[str, Any]:
    if not settings.prometheus_url:
        return {"source": "prometheus", "configured": False, "items": [], "error": "PROMETHEUS_URL is not configured"}
    namespace, name = _label(settings.k8s_namespace), _label(service)
    selector = f'namespace="{namespace}",deployment="{name}"'
    pods = f'namespace="{namespace}",pod=~"{name}-.*"'
    queries = {
        "configured_query": _render(settings.prometheus_query, service),
        "replicas_desired": f"kube_deployment_spec_replicas{{{selector}}}",
        "replicas_available": f"kube_deployment_status_replicas_available{{{selector}}}",
        "container_restarts_5m": f"sum by (pod) (increase(kube_pod_container_status_restarts_total{{{pods}}}[5m]))",
        "containers_waiting": f"kube_pod_container_status_waiting_reason{{{pods}}} == 1",
        "last_terminated_reason": f"kube_pod_container_status_last_terminated_reason{{{pods}}} == 1",
    }
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            for label, query in queries.items():
                try:
                    response = await http.get(f"{settings.prometheus_url}/api/v1/query", params={"query": query})
                    response.raise_for_status()
                    result = response.json().get("data", {}).get("result", [])[:15]
                    items.append({"name": label, "query": query, "result": result})
                except Exception as exc:
                    errors.append(f"{label}: {type(exc).__name__}")
    except Exception as exc:
        errors.append(type(exc).__name__)
    evidence: dict[str, Any] = {"source": "prometheus", "configured": True, "collected_at": _now(), "items": items}
    if not items:
        evidence["error"] = "query failed: " + ", ".join(errors or ["no response"])
    elif errors:
        evidence["partial_errors"] = errors
    return evidence


async def loki_evidence(service: str) -> dict[str, Any]:
    if not settings.loki_url:
        return {"source": "loki", "configured": False, "items": [], "error": "LOKI_URL is not configured"}
    query = _render(settings.loki_query, service)
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=10)
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            response = await http.get(f"{settings.loki_url}/loki/api/v1/query_range", params={
                "query": query, "start": str(int(start.timestamp() * 1e9)),
                "end": str(int(end.timestamp() * 1e9)), "limit": "50", "direction": "backward"})
            response.raise_for_status()
            payload = response.json()
        lines: list[dict[str, str]] = []
        for stream in payload.get("data", {}).get("result", [])[:20]:
            labels = stream.get("stream", {})
            origin = labels.get("pod") or labels.get("container") or labels.get("app") or ""
            for ts, line in stream.get("values", []):
                lines.append({"ts": str(ts), "pod": str(origin), "line": str(line)[:600]})
        lines.sort(key=lambda item: item["ts"], reverse=True)
        return {"source": "loki", "configured": True, "collected_at": _now(), "query": query,
                "window_minutes": 10, "items": lines[:50]}
    except Exception as exc:
        return {"source": "loki", "configured": True, "items": [], "error": f"query failed: {type(exc).__name__}"}


async def prometheus_health(service: str) -> dict[str, Any]:
    if not settings.prometheus_url:
        return {"configured": False, "healthy": False, "reason": "Prometheus is not configured"}
    query = _render(settings.prometheus_health_query, service)
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            response = await http.get(f"{settings.prometheus_url}/api/v1/query", params={"query": query})
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("data", {}).get("result", [])
        values = [float(row["value"][1]) for row in rows if row.get("value")]
        return {"configured": True, "healthy": bool(values) and all(v > 0 for v in values),
                "query": query, "series": len(values), "values": values[:20]}
    except Exception as exc:
        return {"configured": True, "healthy": False, "reason": f"health query failed: {type(exc).__name__}"}


async def prometheus_unhealthy_deployments() -> dict[str, Any]:
    if not settings.prometheus_url:
        return {"configured": False, "items": [], "error": "Prometheus is not configured"}
    namespace = settings.k8s_namespace.replace('"', '')
    queries = {
        "replica_shortfall": (f'(kube_deployment_spec_replicas{{namespace="{namespace}"}} - '
                              f'kube_deployment_status_replicas_available{{namespace="{namespace}"}}) > 0'),
        "crash_loop": f'kube_pod_container_status_waiting_reason{{namespace="{namespace}",reason="CrashLoopBackOff"}} == 1',
        "restarts": f'sum by (pod) (increase(kube_pod_container_status_restarts_total{{namespace="{namespace}"}}[5m])) > 3',
        "deployments": f'kube_deployment_spec_replicas{{namespace="{namespace}"}}',
    }
    try:
        services: set[str] = set()
        signals: dict[str, dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=8) as http:
            responses = {}
            for name, query in queries.items():
                response = await http.get(f"{settings.prometheus_url}/api/v1/query", params={"query": query})
                response.raise_for_status()
                responses[name] = response.json().get("data", {}).get("result", [])
        for row in responses["deployments"]:
            name = row.get("metric", {}).get("deployment")
            if name and not name.startswith(INFRASTRUCTURE_PREFIXES):
                services.add(name)
        for row in responses["replica_shortfall"]:
            labels = row.get("metric", {})
            service = labels.get("deployment")
            if service and service in services:
                signals[service] = {"service": service, "namespace": labels.get("namespace", namespace),
                                    "missing_replicas": float(row.get("value", [0, 0])[1]),
                                    "signals": ["deployment_replica_shortfall"]}
        for signal_name in ("crash_loop", "restarts"):
            for row in responses[signal_name]:
                pod = row.get("metric", {}).get("pod", "")
                service = next((name for name in sorted(services, key=len, reverse=True)
                                if pod.startswith(name + "-")), None)
                if service:
                    item = signals.setdefault(service, {"service": service, "namespace": namespace,
                        "missing_replicas": 0, "signals": []})
                    signal = "crash_loop" if signal_name == "crash_loop" else "repeated_container_restarts"
                    if signal not in item["signals"]:
                        item["signals"].append(signal)
                    if pod not in item.setdefault("affected_pods", []):
                        item["affected_pods"].append(pod)
        return {"configured": True, "queries": queries, "items": list(signals.values()),
                "services": sorted(services)}
    except Exception as exc:
        return {"configured": True, "items": [], "error": f"monitor query failed: {type(exc).__name__}"}


async def prometheus_health_window(service: str, window_minutes: int, since: datetime | None = None) -> dict[str, Any]:
    """Evaluate every Prometheus sample in a bounded recovery window."""
    if not settings.prometheus_url:
        return {"configured": False, "stable": False, "reason": "Prometheus is not configured"}
    end = datetime.now(timezone.utc)
    start = max(end - timedelta(minutes=window_minutes), since or end - timedelta(minutes=window_minutes))
    elapsed = max(0.0, (end - start).total_seconds())
    query = _render(settings.prometheus_health_query, service)
    try:
        async with httpx.AsyncClient(timeout=12) as http:
            response = await http.get(f"{settings.prometheus_url}/api/v1/query_range", params={
                "query": query, "start": str(start.timestamp()), "end": str(end.timestamp()), "step": "30s"})
            response.raise_for_status()
            payload = response.json()
        series = payload.get("data", {}).get("result", [])
        values = [float(sample[1]) for row in series for sample in row.get("values", []) if len(sample) > 1]
        healthy = sum(value > 0 for value in values)
        enough_time = elapsed >= window_minutes * 60
        required_samples = max(1, min(settings.verification_minimum_samples, int(elapsed // 30)))
        enough_samples = len(values) >= required_samples
        return {"configured": True, "stable": enough_time and enough_samples and healthy == len(values),
                "query": query, "window_minutes": window_minutes, "elapsed_seconds": round(elapsed),
                "samples": len(values), "healthy_samples": healthy,
                "minimum_samples": required_samples,
                "reason": None if enough_time and enough_samples and healthy == len(values) else
                    "Recovery window is incomplete, under-sampled, or contains an unhealthy interval"}
    except Exception as exc:
        return {"configured": True, "stable": False, "window_minutes": window_minutes,
                "reason": f"range query failed: {type(exc).__name__}"}


def _load_kube() -> None:
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config(context=settings.k8s_context or None)


def kubernetes_evidence(service: str) -> dict[str, Any]:
    """Blocking read-only Kubernetes query; call through `asyncio.to_thread`."""
    try:
        _load_kube()
        v1 = client.CoreV1Api()
        apps = client.AppsV1Api()
        namespace = settings.k8s_namespace
        deployments = apps.list_namespaced_deployment(namespace=namespace, limit=100, _request_timeout=6).items
        selected = [d for d in deployments if service.lower() in d.metadata.name.lower()]
        pods = v1.list_namespaced_pod(namespace=namespace, limit=200, _request_timeout=6).items
        selected_pods = [p for p in pods if service.lower() in p.metadata.name.lower()]
        events = v1.list_namespaced_event(namespace=namespace, limit=100, _request_timeout=6).items
        return {"source": "kubernetes", "configured": True, "collected_at": _now(),
                "deployments": [{"name": d.metadata.name, "desired": d.spec.replicas,
                                 "ready": d.status.ready_replicas or 0,
                                 "available": d.status.available_replicas or 0} for d in selected],
                "pods": [{"name": p.metadata.name, "phase": p.status.phase,
                          "restarts": sum(c.restart_count for c in (p.status.container_statuses or [])),
                          "reasons": [c.state.waiting.reason for c in (p.status.container_statuses or [])
                                      if c.state and c.state.waiting]} for p in selected_pods],
                "events": [{"reason": e.reason, "message": (e.message or "")[:500],
                            "type": e.type, "object": e.involved_object.name}
                           for e in events if service.lower() in (e.involved_object.name or "").lower()][:30]}
    except ConfigException:
        return {"source": "kubernetes", "configured": False, "deployments": [], "pods": [], "events": [],
                "error": "No kubeconfig/observer credential is available"}
    except Exception as exc:
        return {"source": "kubernetes", "configured": True, "deployments": [], "pods": [], "events": [],
                "error": f"read failed: {type(exc).__name__}"}


async def prometheus_fleet() -> dict[str, Any]:
    """Desired/available replicas for every workload deployment in the monitored namespace."""
    if not settings.prometheus_url:
        return {"configured": False, "deployments": [], "error": "Prometheus is not configured"}
    namespace = _label(settings.k8s_namespace)
    try:
        rows: dict[str, list[dict[str, Any]]] = {}
        async with httpx.AsyncClient(timeout=6) as http:
            for key, metric in (("desired", "kube_deployment_spec_replicas"),
                                ("available", "kube_deployment_status_replicas_available")):
                response = await http.get(f"{settings.prometheus_url}/api/v1/query",
                                          params={"query": f'{metric}{{namespace="{namespace}"}}'})
                response.raise_for_status()
                rows[key] = response.json().get("data", {}).get("result", [])
        available = {row.get("metric", {}).get("deployment"): float(row["value"][1]) for row in rows["available"] if row.get("value")}
        deployments = []
        for row in rows["desired"]:
            name = row.get("metric", {}).get("deployment")
            if not name or name.startswith(INFRASTRUCTURE_PREFIXES):
                continue
            desired = float(row["value"][1])
            ready = available.get(name, 0.0)
            deployments.append({"name": name, "desired": desired, "available": ready, "healthy": ready >= desired})
        return {"configured": True, "checked_at": _now(), "deployments": sorted(deployments, key=lambda d: d["name"])}
    except Exception as exc:
        return {"configured": True, "deployments": [], "error": f"fleet query failed: {type(exc).__name__}"}


_probe_cache: dict[str, tuple[float, dict[str, Any]]] = {}


async def _http_ready(name: str, url: str, path: str) -> dict[str, Any]:
    if not url:
        return {"reachable": False}
    cached = _probe_cache.get(name)
    if cached and time.monotonic() - cached[0] < 10:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            response = await http.get(url + path)
        result = {"reachable": response.status_code < 500}
    except Exception as exc:
        result = {"reachable": False, "reason": f"unreachable ({type(exc).__name__})"}
    _probe_cache[name] = (time.monotonic(), result)
    return result


def _kubernetes_probe() -> dict[str, Any]:
    try:
        _load_kube()
        client.AppsV1Api().list_namespaced_deployment(namespace=settings.k8s_namespace, limit=1, _request_timeout=3)
        return {"configured": True, "reachable": True}
    except ConfigException:
        return {"configured": False, "reachable": False, "reason": "no kubeconfig / observer credential"}
    except Exception as exc:
        return {"configured": True, "reachable": False, "reason": f"unreachable ({type(exc).__name__})"}


async def kubernetes_probe() -> dict[str, Any]:
    cached = _probe_cache.get("kubernetes")
    if cached and time.monotonic() - cached[0] < 15:
        return cached[1]
    result = await asyncio.to_thread(_kubernetes_probe)
    _probe_cache["kubernetes"] = (time.monotonic(), result)
    return result


async def integration_status() -> dict[str, Any]:
    prometheus, loki, kubernetes = await asyncio.gather(
        _http_ready("prometheus", settings.prometheus_url, "/-/ready"),
        _http_ready("loki", settings.loki_url, "/ready"), kubernetes_probe())
    return {
        "kubernetes": {**kubernetes, "mode": "read-only observer", "detail": settings.k8s_namespace},
        "prometheus": {"configured": bool(settings.prometheus_url), **prometheus, "detail": settings.prometheus_url or None},
        "loki": {"configured": bool(settings.loki_url), **loki, "detail": settings.loki_url or None},
        "github": {"configured": bool(settings.github_token), "reachable": bool(settings.github_token)},
        "model": {"configured": bool(settings.llm_model and settings.llm_api_key),
                  "reachable": bool(settings.llm_model and settings.llm_api_key), "detail": settings.llm_model or None},
        "argocd": {"configured": False, "reachable": False, "mode": "read-only", "detail": settings.argocd_application},
        "evaluator": {"configured": bool(settings.evaluator_url), "reachable": bool(settings.evaluator_url),
                      "detail": "hidden-test evaluator (not part of this prototype)"},
    }
