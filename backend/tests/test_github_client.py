"""GitHub client behaviour against the simulated REST API."""
from __future__ import annotations

import asyncio

import pytest

from app.integrations.github import GitHubClient, GitHubError
from simulator.main import world

REPO, PATH = "sim-lab/online-boutique", "deploy/online-boutique/frontend.yaml"


def run(coro):
    return asyncio.run(coro)


def patch(content: str = None):
    source = run(GitHubClient().get_file(REPO, "main", PATH))
    return [{"path": PATH, "content": content or source["content"].replace("replicas: 1", "replicas: 2"), "sha": source["sha"]}]


def test_pull_request_goes_to_a_branch_and_never_to_main():
    pr = run(GitHubClient().create_pull_request(REPO, "main", "t", "b", patch(), "abcdef1234"))
    assert pr["state"] == "open" and pr["merged"] is False and pr["head_branch"] == "incident-to-pr/abcdef12"
    assert "replicas: 1" in world.branches["main"]["files"][PATH]
    assert "replicas: 2" in world.branches["incident-to-pr/abcdef12"]["files"][PATH]


def test_retry_after_a_half_finished_attempt_resumes_on_the_clean_branch():
    client = GitHubClient()
    base = world.branches["main"]["sha"]
    world.branches["incident-to-pr/abcdef12"] = {"files": dict(world.branches["main"]["files"]), "sha": base}
    pr = run(client.create_pull_request(REPO, "main", "t", "b", patch(), "abcdef1234"))
    assert pr["number"] == 1


def test_existing_branch_with_foreign_commits_is_refused():
    world.branches["incident-to-pr/abcdef12"] = {"files": dict(world.branches["main"]["files"]), "sha": "someone-else"}
    with pytest.raises(GitHubError, match="already exists"):
        run(GitHubClient().create_pull_request(REPO, "main", "t", "b", patch(), "abcdef1234"))
    assert world.pulls == []


def test_stale_base_revision_is_refused_with_guidance():
    stale = patch()
    world.branches["main"]["files"][PATH] += "# changed by someone else\n"
    with pytest.raises(GitHubError, match="re-run the analysis"):
        run(GitHubClient().create_pull_request(REPO, "main", "t", "b", stale, "abcdef1234"))


def test_client_validates_inputs_and_reports_missing_files():
    client = GitHubClient()
    with pytest.raises(GitHubError):
        run(client.get_repository("not a repo"))
    with pytest.raises(GitHubError):
        run(client.create_pull_request(REPO, "main", "t", "b", [], "abcdef1234"))
    with pytest.raises(GitHubError) as error:
        run(client.get_file(REPO, "main", "missing.yaml"))
    assert error.value.status == 404


def test_client_requires_a_token(override):
    override(github_token="")
    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        GitHubClient()
