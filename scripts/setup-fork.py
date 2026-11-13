#!/usr/bin/env python3
"""Prepare your Online Boutique fork for Incident-to-PR.

    python scripts/setup-fork.py YOUR_GITHUB_USER/microservices-demo

Run it after forking https://github.com/GoogleCloudPlatform/microservices-demo on GitHub.
It uses the git credentials already configured on this computer and:

  1. clones your fork into a temporary directory,
  2. adds `deploy/online-boutique/` (one file per Deployment, the layout the policy gate and
     Argo CD expect) from `lab/gitops/online-boutique/`,
  3. commits and pushes that single commit to the fork's base branch,
  4. records the repository in this project's `.env` (repository names only, never a token).

Options:
  --pin         first move the base branch to the upstream v0.10.7 tag, so the source matches the
                images the lab runs. This rewrites the fork's branch (force push); use it only on
                a fresh fork you have not committed to.
  --branch B    base branch of the fork (default: main)
  --dry-run     do everything except push and editing .env
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = "https://github.com/GoogleCloudPlatform/microservices-demo.git"
TAG = "v0.10.7"
SOURCE = ROOT / "lab" / "gitops" / "online-boutique"


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def update_env(repository: str, branch: str) -> None:
    env = ROOT / ".env"
    if not env.exists():
        shutil.copyfile(ROOT / ".env.example", env)
    text = env.read_text(encoding="utf-8")
    for key, value in (("GITHUB_CODE_REPOSITORY", repository), ("GITHUB_GITOPS_REPOSITORY", repository), ("GITHUB_BASE_BRANCH", branch)):
        line = f"{key}={value}"
        text, count = re.subn(rf"(?m)^{key}=.*$", line, text)
        if not count:
            text = text.rstrip("\n") + f"\n{line}\n"
    env.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare your Online Boutique fork for Incident-to-PR.")
    parser.add_argument("repository", help="your fork as OWNER/REPO")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--pin", action="store_true", help=f"reset the base branch to upstream {TAG} (force push)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remote-url", help=argparse.SUPPRESS)  # for tests: clone from a local path
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        raise SystemExit("Repository must be OWNER/REPO, for example you/microservices-demo")
    if args.repository.lower().startswith("googlecloudplatform/"):
        raise SystemExit("That is the upstream repository. Fork it on GitHub first, then pass YOUR fork.")
    if not shutil.which("git"):
        raise SystemExit("git was not found on PATH")
    if not (SOURCE / "kustomization.yaml").is_file():
        raise SystemExit("lab/gitops/online-boutique is missing; run scripts/prepare-gitops.py first")

    url = args.remote_url or f"https://github.com/{args.repository}.git"
    with tempfile.TemporaryDirectory(prefix="incident-to-pr-fork-") as tmp:
        work = Path(tmp) / "fork"
        print(f"Cloning {url} …")
        subprocess.run(["git", "clone", "--quiet", "--branch", args.branch, url, str(work)], check=True)
        if args.pin:
            print(f"Pinning {args.branch} to upstream {TAG} …")
            git("fetch", "--quiet", args.remote_url and "origin" or UPSTREAM, f"refs/tags/{TAG}:refs/tags/{TAG}", cwd=work)
            git("checkout", "--quiet", "-B", args.branch, TAG, cwd=work)
        target = work / "deploy" / "online-boutique"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(SOURCE, target)
        git("add", "deploy/online-boutique", cwd=work)
        if git("status", "--porcelain", "deploy/online-boutique", cwd=work):
            git("-c", "user.name=Incident-to-PR setup", "-c", "user.email=incident-to-pr-setup@users.noreply.github.com",
                "commit", "--quiet", "-m", "Add GitOps manifests for the Incident-to-PR lab (deploy/online-boutique)", cwd=work)
            print("Committed deploy/online-boutique.")
        else:
            print("deploy/online-boutique is already up to date.")
        if args.dry_run:
            print("Dry run: nothing was pushed and .env was not changed.")
            return 0
        git("push", *(["--force-with-lease"] if args.pin else []), "origin", f"HEAD:{args.branch}", cwd=work)
    update_env(args.repository, args.branch)
    print(f"\nDone. {args.repository}@{args.branch} now contains deploy/online-boutique, and .env points at it.\n"
          "Next: add GITHUB_TOKEN and LLM_API_KEY to .env yourself, then run .\\scripts\\lab-up.ps1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
