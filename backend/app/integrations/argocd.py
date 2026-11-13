from __future__ import annotations

import asyncio
from typing import Any

import httpx
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException

from ..config import settings


def _read_application() -> dict[str, Any]:
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config(context=settings.k8s_context or None)
    return client.CustomObjectsApi().get_namespaced_custom_object(
        group="argoproj.io", version="v1alpha1", namespace="argocd",
        plural="applications", name=settings.argocd_application, _request_timeout=6)


async def application_status() -> dict[str, Any]:
    """Read-only Argo CD Application status: Kubernetes API first, Argo CD REST API as fallback."""
    try:
        payload = await asyncio.to_thread(_read_application)
        sync = payload.get("status", {}).get("sync", {}).get("status", "Unknown")
        health = payload.get("status", {}).get("health", {}).get("status", "Unknown")
        return {"configured": True, "synced": sync == "Synced", "healthy": health == "Healthy",
                "sync_status": sync, "health_status": health,
                "revision": payload.get("status", {}).get("sync", {}).get("revision")}
    except ApiException as exc:
        if exc.status == 404:
            return {"configured": False, "synced": False, "healthy": False,
                    "reason": f"Argo CD application {settings.argocd_application!r} is not installed"}
        if not (settings.argocd_url and settings.argocd_token):
            return {"configured": True, "synced": False, "healthy": False,
                    "reason": f"Read-only Kubernetes API query failed ({exc.status})"}
    except Exception as exc:
        if not settings.argocd_url or not settings.argocd_token:
            return {"configured": False, "synced": False, "healthy": False,
                    "reason": f"Read-only Kubernetes API unavailable: {type(exc).__name__}"}
    try:
        async with httpx.AsyncClient(timeout=10, verify=settings.argocd_verify_tls) as http:
            response = await http.get(
                f"{settings.argocd_url}/api/v1/applications/{settings.argocd_application}",
                headers={"Authorization": f"Bearer {settings.argocd_token}"})
            response.raise_for_status()
            payload = response.json()
        sync = payload.get("status", {}).get("sync", {}).get("status", "Unknown")
        health = payload.get("status", {}).get("health", {}).get("status", "Unknown")
        return {"configured": True, "synced": sync == "Synced", "healthy": health == "Healthy",
                "sync_status": sync, "health_status": health,
                "revision": payload.get("status", {}).get("sync", {}).get("revision")}
    except Exception as exc:
        return {"configured": True, "synced": False, "healthy": False,
                "reason": f"Argo CD status query failed: {type(exc).__name__}"}
