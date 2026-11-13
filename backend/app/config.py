from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load the repository-root .env so `uvicorn app.main:app` works on any platform without a
# wrapper script. Variables already present in the process environment always win.
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env", override=False)

SIMULATION = os.getenv("APP_ENV", "development").lower() == "simulation"


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(value.strip().strip("/") for value in os.getenv(name, default).split(",") if value.strip())


def _int(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        value = default
    return value if minimum is None else max(minimum, value)


def _database_path() -> Path:
    path = Path(os.getenv("DATABASE_PATH", "./data/incident-to-pr.sqlite3"))
    # Relative paths are anchored at the repository root, not the process working directory.
    return path if path.is_absolute() else ROOT_DIR / path


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = _int("APP_PORT", 8000)
    database_path: Path = _database_path()
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_api_base: str = os.getenv("LLM_API_BASE", "")
    llm_timeout_seconds: int = _int("LLM_TIMEOUT_SECONDS", 120, 1)
    llm_max_tokens: int = _int("LLM_MAX_TOKENS", 16000, 256)
    # Empty = automatic: 0 for most providers, omitted for models that reject sampling parameters.
    llm_temperature: str = os.getenv("LLM_TEMPERATURE", "")
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "")
    llm_fallback_api_key: str = os.getenv("LLM_FALLBACK_API_KEY", "")
    llm_fallback_api_base: str = os.getenv("LLM_FALLBACK_API_BASE", "")
    llm_trace_enabled: bool = os.getenv("LLM_TRACE_ENABLED", "true").lower() == "true"
    k8s_namespace: str = os.getenv("K8S_NAMESPACE", "online-boutique")
    k8s_context: str = os.getenv("K8S_CONTEXT", "")
    prometheus_url: str = os.getenv("PROMETHEUS_URL", "")
    prometheus_query: str = os.getenv("PROMETHEUS_QUERY", "up")
    prometheus_health_query: str = os.getenv("PROMETHEUS_HEALTH_QUERY", 'up{service="$service"}')
    # The 15-minute / 10-sample floors can only be lowered in the synthetic simulation lab.
    verification_window_minutes: int = _int("VERIFICATION_WINDOW_MINUTES", 15, 1 if SIMULATION else 15)
    verification_minimum_samples: int = _int("VERIFICATION_MINIMUM_SAMPLES", 20, 2 if SIMULATION else 10)
    monitor_poll_seconds: int = _int("MONITOR_POLL_SECONDS", 30, 2 if SIMULATION else 15)
    loki_url: str = os.getenv("LOKI_URL", "")
    loki_query: str = os.getenv("LOKI_QUERY", '{namespace="online-boutique",pod=~"$service-.*"}')
    alertmanager_webhook_token: str = os.getenv("ALERTMANAGER_WEBHOOK_TOKEN", "")
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_argocd_read_token: str = os.getenv("GITHUB_ARGOCD_READ_TOKEN", "")
    github_api_url: str = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    github_code_repository: str = os.getenv("GITHUB_CODE_REPOSITORY", "")
    github_gitops_repository: str = os.getenv("GITHUB_GITOPS_REPOSITORY", "")
    github_base_branch: str = os.getenv("GITHUB_BASE_BRANCH", "main")
    github_bot_name: str = os.getenv("GITHUB_BOT_NAME", "Incident-to-PR Bot")
    github_bot_email: str = os.getenv("GITHUB_BOT_EMAIL", "incident-to-pr[bot]@users.noreply.github.com")
    code_allowed_paths: tuple[str, ...] = _csv("CODE_ALLOWED_PATHS", "src/$service/")
    infra_allowed_paths: tuple[str, ...] = _csv("INFRA_ALLOWED_PATHS", "deploy/online-boutique/")
    code_context_files: tuple[str, ...] = _csv("CODE_CONTEXT_FILES", "src/$service/main.go")
    infra_context_files: tuple[str, ...] = _csv("INFRA_CONTEXT_FILES", "deploy/online-boutique/$service.yaml")
    # Optional per-service override of CODE_CONTEXT_FILES (JSON: {"services": {"<name>": {"code": [...]}}}).
    service_context_map: Path = (lambda p: p if p.is_absolute() else ROOT_DIR / p)(
        Path(os.getenv("SERVICE_CONTEXT_MAP", "lab/service-context.json")))
    max_patch_files: int = _int("MAX_PATCH_FILES", 3, 1)
    max_patch_bytes: int = _int("MAX_PATCH_BYTES", 200000, 1)
    argocd_url: str = os.getenv("ARGOCD_URL", "").rstrip("/")
    argocd_application: str = os.getenv("ARGOCD_APPLICATION", "online-boutique")
    argocd_token: str = os.getenv("ARGOCD_TOKEN", "")
    argocd_verify_tls: bool = os.getenv("ARGOCD_VERIFY_TLS", "true").lower() == "true"
    evaluator_url: str = os.getenv("EVALUATOR_URL", "").rstrip("/")
    evaluator_token: str = os.getenv("EVALUATOR_TOKEN", "")
    require_human_approval: bool = True
    max_open_incidents: int = _int("MAX_OPEN_INCIDENTS", 20, 1)
    evidence_max_age_seconds: int = _int("EVIDENCE_MAX_AGE_SECONDS", 1800, 60)
    auto_apply_enabled: bool = False

    @property
    def simulation(self) -> bool:
        return self.app_env.lower() == "simulation"


settings = Settings()
