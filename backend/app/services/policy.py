"""Deterministic policy gate.

The gate inspects the actual proposed file contents, never the model's description of them.
Every rule fails closed: an unknown action, an unparsable manifest, or a missing base revision
denies the proposal. A passing result still always requires human approval.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

import yaml

from ..config import settings
from .runtime_config import for_service

RULE_VERSION = "phase1-v2"
FORBIDDEN_PARTS = {".github/", "secret", "rbac", "networkpolicy", "clusterrole", "serviceaccount",
                   "argocd", "policy/", "evaluator", "hidden"}
ALLOWED_DECISIONS = {"INFRA_FIX", "CODE_FIX", "ROLLBACK", "ESCALATE_HUMAN"}
MIN_REPLICAS, MAX_REPLICAS = 1, 10
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}"),
    re.compile(r"(?im)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*[\"']?(?!\$\{|\{\{)[^\"'\s,;#]{12,}"),
)


def _normalize(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix().lstrip("/")


def _within(path: str, prefixes: tuple[str, ...]) -> bool:
    normalized = _normalize(path)
    return ".." not in PurePosixPath(normalized).parts and any(
        normalized == p or normalized.startswith(p.rstrip("/") + "/") for p in prefixes if p)


def _changed_paths(before: Any, after: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changed: list[tuple[str, ...]] = []
        for key in sorted(before.keys() | after.keys(), key=str):
            if key not in before or key not in after:
                changed.append((*prefix, str(key)))
            else:
                changed.extend(_changed_paths(before[key], after[key], (*prefix, str(key))))
        return changed
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [prefix]
        changed = []
        for index, (old_item, new_item) in enumerate(zip(before, after)):
            changed.extend(_changed_paths(old_item, new_item, (*prefix, str(index))))
        return changed
    # bool is a subclass of int in Python; `replicas: true` must not compare equal to `replicas: 1`.
    return [] if before == after and type(before) is type(after) else [prefix]


def _deny(reasons: list[str], checks: list[dict[str, Any]] | None = None, requires_approval: bool = False) -> dict[str, Any]:
    return {"allowed": False, "requires_approval": requires_approval, "reasons": reasons,
            "checks": checks or [], "rule_version": RULE_VERSION}


def _infra_reasons(path: str, base_content: str, content: str) -> list[str]:
    reasons: list[str] = []
    try:
        base_docs = list(yaml.safe_load_all(base_content))
        proposed_docs = list(yaml.safe_load_all(content))
    except yaml.YAMLError:
        return [f"Invalid YAML: {path}"]
    if len(base_docs) != 1 or len(proposed_docs) != 1 or not isinstance(base_docs[0], dict) or not isinstance(proposed_docs[0], dict):
        return [f"Infrastructure patch must replace a single YAML object: {path}"]
    base_doc, proposed_doc = base_docs[0], proposed_docs[0]
    if base_doc.get("kind") != "Deployment":
        reasons.append(f"Only Deployment manifests may be changed: {path}")
    for field in ("apiVersion", "kind"):
        if base_doc.get(field) != proposed_doc.get(field):
            reasons.append(f"Infrastructure identity field cannot change: {path} ({field})")
    base_meta = base_doc.get("metadata") if isinstance(base_doc.get("metadata"), dict) else {}
    new_meta = proposed_doc.get("metadata") if isinstance(proposed_doc.get("metadata"), dict) else {}
    for field in ("name", "namespace"):
        if base_meta.get(field) != new_meta.get(field):
            reasons.append(f"Infrastructure object identity cannot change: {path} ({field})")
    changed = _changed_paths(base_doc, proposed_doc)
    if not changed:
        reasons.append(f"Infrastructure patch has no effective change: {path}")
    for field_path in changed:
        if field_path != ("spec", "replicas"):
            reasons.append(f"Infrastructure field is outside the Phase I change policy: {path} ({'.'.join(field_path) or 'document'})")
    spec = proposed_doc.get("spec") if isinstance(proposed_doc.get("spec"), dict) else {}
    replicas = spec.get("replicas")
    if ("spec", "replicas") in changed and (
            type(replicas) is not int or replicas < MIN_REPLICAS or replicas > MAX_REPLICAS):
        reasons.append(f"Replica count must be an integer from {MIN_REPLICAS} to {MAX_REPLICAS}: {path}")
    return reasons


def validate_proposal(decision: dict[str, Any], patches: list[dict[str, str]], service: str = "") -> dict[str, Any]:
    action = decision.get("action") if isinstance(decision, dict) else None
    if action not in ALLOWED_DECISIONS:
        return _deny(["Unknown decision type"])
    if action == "ESCALATE_HUMAN":
        return _deny(["Decision requires human escalation"], requires_approval=True)
    if action == "ROLLBACK":
        return _deny(["Rollback is a human-operated action in this phase"], requires_approval=True)
    if not isinstance(patches, list) or not patches:
        return _deny(["No patch was generated"])
    if len(patches) > settings.max_patch_files:
        return _deny([f"Patch changes too many files (limit {settings.max_patch_files})"])
    if any(not isinstance(p, dict) or not isinstance(p.get("content"), str) or not isinstance(p.get("path"), str) for p in patches):
        return _deny(["Patch entries must contain a text path and text content"])
    total = sum(len(p["content"].encode("utf-8")) for p in patches)
    if total > settings.max_patch_bytes:
        return _deny([f"Patch exceeds byte limit ({settings.max_patch_bytes})"])

    allowed_prefixes = for_service(settings.infra_allowed_paths if action == "INFRA_FIX" else settings.code_allowed_paths, service)
    buckets: dict[str, list[str]] = {name: [] for name in (
        "Safe file paths", "Path allowlist", "Protected resources", "No credentials in patch",
        "Change scope", "Pinned base revision")}
    seen_paths: set[str] = set()
    for patch in patches:
        path = patch["path"]
        normalized = path.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if not path or path.startswith(("/", "\\")) or ".." in parts or "\x00" in path or ":" in path or path != path.strip():
            buckets["Safe file paths"].append(f"Unsafe path: {path!r}")
            continue
        if normalized in seen_paths:
            buckets["Safe file paths"].append(f"Duplicate patch path: {normalized}")
        seen_paths.add(normalized)
        if any(word in normalized.lower() for word in FORBIDDEN_PARTS):
            buckets["Protected resources"].append(f"Protected path is forbidden: {path}")
        content = patch["content"]
        if "\x00" in content:
            buckets["Change scope"].append(f"Binary content is not allowed: {path}")
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            buckets["No credentials in patch"].append(f"Patch appears to contain a credential or private key: {path}")
        if not _within(normalized, allowed_prefixes):
            kind = "Infrastructure" if action == "INFRA_FIX" else "Code"
            buckets["Path allowlist"].append(f"{kind} path is outside allowlist: {path}")
        base_content = patch.get("base_content")
        if action == "INFRA_FIX":
            if not normalized.lower().endswith((".yaml", ".yml")):
                buckets["Change scope"].append(f"Infrastructure changes must target YAML manifests: {path}")
            buckets["Change scope"].extend(_infra_reasons(path, base_content if isinstance(base_content, str) else "", content))
        elif isinstance(base_content, str) and base_content == content:
            buckets["Change scope"].append(f"Code patch has no effective change: {path}")
        if not patch.get("sha") or not isinstance(base_content, str):
            buckets["Pinned base revision"].append(f"Missing base file revision: {path}")

    checks = [{"name": name, "passed": not problems, "detail": "; ".join(problems)} for name, problems in buckets.items()]
    reasons = [problem for problems in buckets.values() for problem in problems]
    if reasons:
        return _deny(reasons, checks)
    return {"allowed": True, "requires_approval": True, "reasons": [], "checks": checks,
            "rule_version": RULE_VERSION, "patch_files": [p["path"] for p in patches], "patch_bytes": total}
