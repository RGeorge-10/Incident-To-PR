from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from ..config import settings
from ..database import get_app_setting

SERVICE_NAME = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def normalize_github_repository(value: str) -> str:
    raw = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", raw):
        owner, repository = raw.split("/", 1)
    else:
        parsed = urlparse(raw)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
            raise ValueError("Enter an HTTPS github.com repository link (or owner/repository).")
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) != 2:
            raise ValueError("A repository link must point directly to https://github.com/OWNER/REPOSITORY.")
        owner, repository = parts
    if repository.lower().endswith(".git"):
        repository = repository[:-4]
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repository)
            or owner in {".", ".."} or repository in {".", ".."}):
        raise ValueError("Repository owner or name contains unsupported characters.")
    return f"{owner}/{repository}"


def repository_config() -> dict[str, str]:
    return {
        "code_repository": get_app_setting("code_repository", settings.github_code_repository),
        "gitops_repository": get_app_setting("gitops_repository", settings.github_gitops_repository),
    }


def auto_monitor_config() -> dict[str, bool]:
    return {
        "enabled": get_app_setting("auto_monitor_enabled", "false").lower() == "true",
        "auto_analyze": get_app_setting("auto_analyze_enabled", "false").lower() == "true",
    }


def for_service(templates: tuple[str, ...], service: str) -> tuple[str, ...]:
    """Expand `$service` in configured paths.

    The service name comes from an incident report or alert, so it is untrusted: it is substituted
    only when it is a plain Kubernetes DNS label. Otherwise templated entries are dropped, which
    leaves the allowlist/context empty and makes the workflow fail closed.
    """
    safe = service if SERVICE_NAME.fullmatch(service or "") else None
    expanded = []
    for template in templates:
        if "$service" not in template:
            expanded.append(template)
        elif safe:
            expanded.append(template.replace("$service", safe))
    return tuple(expanded)


@lru_cache(maxsize=4)
def _load_context_map(path: Path) -> dict[str, tuple[str, ...]]:
    try:
        services = json.loads(path.read_text(encoding="utf-8")).get("services", {})
        return {name: tuple(str(f).strip().strip("/") for f in entry.get("code", []) if isinstance(f, str) and f.strip())
                for name, entry in services.items() if isinstance(entry, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def code_context_files(service: str) -> tuple[str, ...]:
    """Source files offered to the model for a code fix: the per-service map first, templates otherwise."""
    if SERVICE_NAME.fullmatch(service or ""):
        mapped = _load_context_map(settings.service_context_map).get(service)
        if mapped:
            return mapped
    return for_service(settings.code_context_files, service)
