from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote

import httpx

from ..config import settings


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    def __init__(self) -> None:
        if not settings.github_token:
            raise GitHubError("GITHUB_TOKEN is not configured")
        self.headers = {"Authorization": f"Bearer {settings.github_token}",
                        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

    async def get_repository(self, repository: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise GitHubError("Repository must be owner/name")
        item = await self._request("GET", f"/repos/{repository}")
        return {"full_name": item.get("full_name"), "private": bool(item.get("private")),
                "default_branch": item.get("default_branch")}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with httpx.AsyncClient(timeout=20) as http:
                response = await http.request(method, settings.github_api_url + path, headers=self.headers, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub API is unreachable ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            raise GitHubError(f"GitHub API returned {response.status_code}: {response.text[:300]}", response.status_code)
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise GitHubError("GitHub API returned a non-JSON response") from exc

    async def get_file(self, repository: str, branch: str, path: str) -> dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise GitHubError("Repository must be owner/name")
        encoded_path = "/".join(quote(segment, safe="") for segment in path.strip("/").split("/"))
        item = await self._request("GET", f"/repos/{repository}/contents/{encoded_path}", params={"ref": branch})
        if not isinstance(item, dict) or item.get("type") != "file" or not item.get("content"):
            raise GitHubError(f"{path} is not a readable text file (directories and files over 1 MB are not supported)")
        try:
            content = base64.b64decode(item["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubError(f"{path} is not UTF-8 text") from exc
        return {"path": path, "sha": item["sha"], "content": content}

    async def create_pull_request(self, repository: str, base: str, title: str, body: str,
                                  patches: list[dict[str, str]], incident_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise GitHubError("Repository must be owner/name")
        if not patches:
            raise GitHubError("No validated patches supplied")
        # Make retries safe after a timeout that happened after GitHub created the PR.
        branch = f"incident-to-pr/{incident_id[:8]}"
        existing = await self._request("GET", f"/repos/{repository}/pulls",
                                       params={"state": "all", "head": f"{repository.split('/')[0]}:{branch}", "base": base})
        if isinstance(existing, list) and existing:
            pr = existing[0]
            return {"number": pr["number"], "url": pr["html_url"], "state": pr["state"],
                    "merged": bool(pr.get("merged_at")), "merged_at": pr.get("merged_at"),
                    "head_branch": branch, "base_branch": base, "repository": repository}
        ref_path = "/".join(quote(segment, safe="") for segment in f"heads/{base}".split("/"))
        ref = await self._request("GET", f"/repos/{repository}/git/ref/{ref_path}")
        base_sha = ref["object"]["sha"]
        try:
            await self._request("POST", f"/repos/{repository}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        except GitHubError as exc:
            # A previous attempt may have created the branch and then failed before opening the PR.
            # Resume only if that branch is still exactly the base commit; never push onto unknown history.
            if exc.status != 422:
                raise
            head_path = "/".join(quote(segment, safe="") for segment in f"heads/{branch}".split("/"))
            head = await self._request("GET", f"/repos/{repository}/git/ref/{head_path}")
            if head.get("object", {}).get("sha") != base_sha:
                raise GitHubError(f"Branch {branch} already exists with other commits; delete it on GitHub, then retry") from exc
        for patch in patches:
            path = "/".join(quote(segment, safe="") for segment in patch["path"].strip("/").split("/"))
            payload = {"message": f"Incident {incident_id[:8]}: propose remediation",
                       "content": base64.b64encode(patch["content"].encode("utf-8")).decode("ascii"),
                       "branch": branch, "committer": {"name": settings.github_bot_name, "email": settings.github_bot_email}}
            payload["sha"] = patch["sha"]
            try:
                await self._request("PUT", f"/repos/{repository}/contents/{path}", json=payload)
            except GitHubError as exc:
                if exc.status == 409:
                    raise GitHubError(f"{patch['path']} changed on {base} after the analysis; re-run the analysis "
                                      "so the proposal is rebuilt on the current revision") from exc
                raise
        pr = await self._request("POST", f"/repos/{repository}/pulls", json={
            "title": title[:250], "body": body[:6000], "head": branch, "base": base, "draft": False})
        return {"number": pr["number"], "url": pr["html_url"], "state": pr["state"],
                "merged": bool(pr.get("merged_at")), "merged_at": pr.get("merged_at"),
                "head_branch": branch, "base_branch": base, "repository": repository}

    async def get_pull_request(self, repository: str, number: int) -> dict[str, Any]:
        pr = await self._request("GET", f"/repos/{repository}/pulls/{number}")
        return {"number": pr["number"], "url": pr["html_url"], "state": pr["state"],
                "merged": bool(pr["merged"]), "merged_at": pr.get("merged_at"),
                "head_sha": pr["head"]["sha"], "base_sha": pr["base"]["sha"]}
