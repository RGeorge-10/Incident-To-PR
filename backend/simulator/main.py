"""Synthetic lab for Incident-to-PR.

One small server that imitates just enough of Prometheus, Loki, the GitHub REST API, the Argo CD
REST API and an OpenAI-compatible chat endpoint for the real control plane to run its complete
workflow on a laptop with no cluster and no credentials.

Everything here is synthetic. The "model" is a deterministic rule-based stub, the telemetry is
generated, and pull requests exist only in memory. It is a functional test fixture and a UI demo;
it is NOT evidence that the system diagnoses or repairs real faults.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

NAMESPACE = "online-boutique"
REPOSITORY = "sim-lab/online-boutique"
PUBLIC_URL = os.getenv("SIM_PUBLIC_URL", "/sim").rstrip("/")
SYNC_DELAY_SECONDS = float(os.getenv("SIM_SYNC_DELAY_SECONDS", "4"))
SERVICES = ("adservice", "cartservice", "checkoutservice", "currencyservice", "emailservice", "frontend",
            "loadgenerator", "paymentservice", "productcatalogservice", "recommendationservice",
            "redis-cart", "shippingservice")
FAULT_KINDS = {
    "capacity": "Single replica is OOM-killed under load; adding replicas restores service (infrastructure fault).",
    "code_bug": "A latent nil-slice bug panics on empty carts; only a source fix restores service (code fault).",
    "ambiguous": "Pods are unavailable with no clear signal; the correct outcome is human escalation.",
}
BUGGY_LINE = "\treturn items[0]\n"
FIXED_LINES = "\tif len(items) == 0 {\n\t\treturn \"\"\n\t}\n\treturn items[0]\n"


def _manifest(service: str, replicas: int = 1) -> str:
    return (f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {service}\n  labels:\n    app: {service}\n"
            f"spec:\n  replicas: {replicas}\n  selector:\n    matchLabels:\n      app: {service}\n  template:\n"
            f"    metadata:\n      labels:\n        app: {service}\n    spec:\n      containers:\n"
            f"        - name: server\n          image: us-central1-docker.pkg.dev/google-samples/microservices-demo/{service}:v0.10.7\n"
            f"          resources:\n            requests:\n              cpu: 100m\n              memory: 64Mi\n"
            f"            limits:\n              cpu: 200m\n              memory: 128Mi\n")


def _source(service: str) -> str:
    return (f"// {service}: synthetic source file for the Incident-to-PR simulation lab.\npackage main\n\n"
            "import (\n\t\"log\"\n\t\"net/http\"\n)\n\n"
            "// firstItem returns the first cart item used to pick a handling route.\n"
            "func firstItem(items []string) string {\n" + BUGGY_LINE + "}\n\n"
            "func handle(w http.ResponseWriter, r *http.Request) {\n"
            "\titems := r.URL.Query()[\"item\"]\n"
            "\tlog.Printf(\"handling request with %d items, first=%q\", len(items), firstItem(items))\n"
            "\tw.WriteHeader(http.StatusOK)\n}\n\n"
            "func main() {\n\thttp.HandleFunc(\"/\", handle)\n\tlog.Fatal(http.ListenAndServe(\":50051\", nil))\n}\n")


def _sha(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


class World:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            files: dict[str, str] = {}
            for service in SERVICES:
                files[f"deploy/online-boutique/{service}.yaml"] = _manifest(service)
                files[f"src/{service}/main.go"] = _source(service)
            self.branches: dict[str, dict[str, Any]] = {"main": {"files": files, "sha": _sha("main-0")}}
            self.pulls: list[dict[str, Any]] = []
            self.faults: dict[str, dict[str, Any]] = {}
            self.pending_sync: float | None = None
            self._live: dict[str, str] | None = None
            self.synced_revision = self.branches["main"]["sha"]
            # Availability history per service: list of (unix_ts, available) transitions.
            self.history: dict[str, list[tuple[float, int]]] = {s: [(time.time() - 86400, 1)] for s in SERVICES}

    # -- derived state ---------------------------------------------------------
    def desired(self, service: str) -> int:
        try:
            doc = yaml.safe_load(self.live_files()[f"deploy/online-boutique/{service}.yaml"])
            return int(doc["spec"]["replicas"])
        except Exception:
            return 1

    def live_files(self) -> dict[str, str]:
        """Files of the revision Argo CD has applied (main lags by the sync delay after a merge)."""
        return self._live or self.branches["main"]["files"]

    def fault_resolved(self, service: str, fault: dict[str, Any]) -> bool:
        files = self.live_files()
        if fault["kind"] == "capacity":
            return self.desired(service) >= 2
        if fault["kind"] == "code_bug":
            return "len(items) == 0" in files.get(f"src/{service}/main.go", "")
        return False

    def available(self, service: str) -> int:
        fault = self.faults.get(service)
        return 0 if fault and not self.fault_resolved(service, fault) else self.desired(service)

    def tick(self) -> None:
        """Apply a due Argo sync and record availability transitions."""
        with self.lock:
            if self.pending_sync is not None and time.time() >= self.pending_sync:
                self.pending_sync = None
                self._live = None
                self.synced_revision = self.branches["main"]["sha"]
                for service, fault in list(self.faults.items()):
                    if self.fault_resolved(service, fault):
                        del self.faults[service]
            for service in SERVICES:
                current = self.available(service)
                if self.history[service][-1][1] != current:
                    self.history[service].append((time.time(), current))

    def available_at(self, service: str, ts: float) -> int | None:
        value = None
        for start, available in self.history[service]:
            if start <= ts:
                value = available
        return value

    def unhealthy(self) -> dict[str, dict[str, Any]]:
        return {s: f for s, f in self.faults.items() if not self.fault_resolved(s, f)}

    def pod(self, service: str) -> str:
        return f"{service}-{_sha(service)[:9]}-{_sha(service + 'pod')[:5]}"


world = World()
app = FastAPI(title="Incident-to-PR simulation lab", version="0.1.0")


@app.middleware("http")
async def _advance_world(request: Request, call_next):
    world.tick()
    return await call_next(request)


# ============================== control surface ===============================
@app.get("/sim/state")
def sim_state() -> dict[str, Any]:
    with world.lock:
        return {
            "simulation": True, "repository": REPOSITORY, "fault_kinds": FAULT_KINDS,
            "services": [{"name": s, "desired": world.desired(s), "available": world.available(s),
                          "fault": (world.faults.get(s) or {}).get("kind")} for s in SERVICES],
            "pulls": [{"number": p["number"], "title": p["title"], "state": p["state"], "merged": bool(p["merged_at"]),
                       "url": p["html_url"]} for p in world.pulls],
            "argocd": _argo_status(),
        }


@app.post("/sim/faults")
async def sim_inject(request: Request) -> dict[str, Any]:
    body = await request.json()
    service, kind = str(body.get("service", "")), str(body.get("kind", ""))
    if service not in SERVICES or kind not in FAULT_KINDS:
        raise HTTPException(422, f"service must be one of {SERVICES} and kind one of {tuple(FAULT_KINDS)}")
    with world.lock:
        world.faults[service] = {"kind": kind, "started": time.time()}
    world.tick()
    return sim_state()


@app.delete("/sim/faults/{service}")
def sim_clear(service: str) -> dict[str, Any]:
    with world.lock:
        world.faults.pop(service, None)
    world.tick()
    return sim_state()


@app.post("/sim/reset")
def sim_reset() -> dict[str, Any]:
    world.reset()
    return sim_state()


# ================================ Prometheus ==================================
def _vector(rows: list[tuple[dict[str, str], float]]) -> dict[str, Any]:
    now = time.time()
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": metric, "value": [now, str(value)]} for metric, value in rows]}}


def _matches(query: str, service: str) -> bool:
    deployment = re.search(r'deployment="([^"]+)"', query)
    pod = re.search(r'pod=~"([^"]+?)-\.\*"', query)
    return (not deployment or deployment.group(1) == service) and (not pod or pod.group(1) == service)


def _evaluate(query: str) -> list[tuple[dict[str, str], float]]:
    base = {"namespace": NAMESPACE}
    selected = [s for s in SERVICES if _matches(query, s)]
    faulty = {s: f for s, f in world.unhealthy().items() if s in selected}
    if "kube_deployment_spec_replicas" in query and "kube_deployment_status_replicas_available" in query:
        return [({**base, "deployment": s}, world.desired(s) - world.available(s)) for s in selected
                if world.desired(s) - world.available(s) > 0]
    if "kube_pod_container_status_waiting_reason" in query:
        return [({**base, "pod": world.pod(s), "container": "server", "reason": "CrashLoopBackOff"}, 1) for s in faulty]
    if "kube_pod_container_status_last_terminated_reason" in query:
        reasons = {"capacity": "OOMKilled", "code_bug": "Error"}
        return [({**base, "pod": world.pod(s), "container": "server", "reason": reasons[f["kind"]]}, 1)
                for s, f in faulty.items() if f["kind"] in reasons]
    if "kube_pod_container_status_restarts_total" in query:
        return [({"pod": world.pod(s)}, min(12.0, 4 + (time.time() - f["started"]) // 20)) for s, f in faulty.items()]
    if "kube_deployment_status_replicas_available" in query:
        return [({**base, "deployment": s}, world.available(s)) for s in selected]
    if "kube_deployment_spec_replicas" in query:
        return [({**base, "deployment": s}, world.desired(s)) for s in selected]
    if query.strip().startswith("up"):
        return [({"job": "kube-state-metrics", "instance": "10.244.0.12:8080"}, 1), ({"job": "prometheus", "instance": "localhost:9090"}, 1)]
    return []


@app.get("/prom/-/ready")
def prom_ready() -> str:
    return "Prometheus Server is Ready (simulated).\n"


@app.get("/prom/api/v1/query")
def prom_query(query: str) -> dict[str, Any]:
    with world.lock:
        return _vector(_evaluate(query))


@app.get("/prom/api/v1/query_range")
def prom_query_range(query: str, start: float, end: float, step: str = "30s") -> dict[str, Any]:
    seconds = max(1.0, float(step.rstrip("s")))
    with world.lock:
        result = []
        if "kube_deployment_status_replicas_available" in query:
            for service in (s for s in SERVICES if _matches(query, s)):
                values, ts = [], start
                while ts <= end + 1e-6 and len(values) < 11000:
                    available = world.available_at(service, ts)
                    if available is not None:
                        values.append([ts, str(available)])
                    ts += seconds
                result.append({"metric": {"namespace": NAMESPACE, "deployment": service}, "values": values})
    return {"status": "success", "data": {"resultType": "matrix", "result": result}}


# ================================== Loki ======================================
LOG_LINES = {
    "capacity": ["level=warn msg=\"request queue saturated\" inflight=240 replicas=1",
                 "level=error msg=\"memory limit exceeded while buffering requests\" rss=127Mi limit=128Mi",
                 "level=info msg=\"container terminated\" reason=OOMKilled exit_code=137"],
    "code_bug": ["level=info msg=\"handling request\" items=0",
                 "panic: runtime error: index out of range [0] with length 0",
                 "goroutine 41 [running]: main.firstItem(...) /src/main.go:12 +0x1d",
                 "main.handle(0xc0001a2000, 0xc0001b4000) /src/main.go:17 +0x8c"],
    "ambiguous": ["level=warn msg=\"upstream connection reset by peer\"",
                  "level=warn msg=\"readiness probe failed\" detail=\"context deadline exceeded\""],
}


@app.get("/loki/ready")
def loki_ready() -> str:
    return "ready\n"


@app.get("/loki/loki/api/v1/query_range")
def loki_query_range(query: str) -> dict[str, Any]:
    streams = []
    now_ns = int(time.time() * 1e9)
    with world.lock:
        for service in SERVICES:
            if not _matches(query, service):
                continue
            fault = world.unhealthy().get(service)
            lines = LOG_LINES[fault["kind"]] if fault else ["level=info msg=\"request served\" status=200 latency_ms=12"]
            values = [[str(now_ns - index * 1_500_000_000), line] for index, line in enumerate(reversed(lines))]
            streams.append({"stream": {"namespace": NAMESPACE, "pod": world.pod(service), "container": "server"}, "values": values})
    return {"status": "success", "data": {"resultType": "streams", "result": streams}}


# ================================= Argo CD ====================================
def _argo_status() -> dict[str, Any]:
    pending = world.pending_sync is not None
    degraded = bool(world.unhealthy())
    return {"sync": {"status": "OutOfSync" if pending else "Synced", "revision": world.synced_revision},
            "health": {"status": "Progressing" if pending else ("Degraded" if degraded else "Healthy")}}


@app.get("/argocd/api/v1/applications/{name}")
def argocd_application(name: str) -> dict[str, Any]:
    if name != "online-boutique":
        raise HTTPException(404, "application not found")
    with world.lock:
        return {"metadata": {"name": name}, "status": _argo_status()}


# ================================== GitHub ====================================
def _gh_error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


def _repo_ok(owner: str, repo: str) -> bool:
    return f"{owner}/{repo}" == REPOSITORY


def _pull_json(pr: dict[str, Any]) -> dict[str, Any]:
    return {"number": pr["number"], "html_url": pr["html_url"], "state": pr["state"], "title": pr["title"],
            "merged": bool(pr["merged_at"]), "merged_at": pr["merged_at"],
            "head": {"ref": pr["head"], "sha": world.branches.get(pr["head"], {}).get("sha", pr["head_sha"])},
            "base": {"ref": pr["base"], "sha": world.branches[pr["base"]]["sha"]}}


@app.get("/github/repos/{owner}/{repo}")
def gh_repo(owner: str, repo: str):
    if not _repo_ok(owner, repo):
        return _gh_error(404, "Not Found")
    return {"full_name": REPOSITORY, "private": False, "default_branch": "main"}


@app.get("/github/repos/{owner}/{repo}/contents/{path:path}")
def gh_get_file(owner: str, repo: str, path: str, ref: str = "main"):
    with world.lock:
        branch = world.branches.get(ref)
        if not _repo_ok(owner, repo) or not branch or path not in branch["files"]:
            return _gh_error(404, "Not Found")
        content = branch["files"][path]
    return {"type": "file", "path": path, "sha": _sha(content), "encoding": "base64",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}


@app.put("/github/repos/{owner}/{repo}/contents/{path:path}")
async def gh_put_file(owner: str, repo: str, path: str, request: Request):
    body = await request.json()
    with world.lock:
        branch = world.branches.get(body.get("branch", ""))
        if not _repo_ok(owner, repo) or not branch:
            return _gh_error(404, "Not Found")
        if branch is world.branches["main"]:
            return _gh_error(409, "Protected branch: changes must be made through a pull request")
        if path in branch["files"] and body.get("sha") != _sha(branch["files"][path]):
            return _gh_error(409, f"{path} does not match {body.get('sha')}")
        content = base64.b64decode(body["content"]).decode("utf-8")
        branch["files"][path] = content
        branch["sha"] = _sha(branch["sha"] + path + content)
        return {"content": {"path": path, "sha": _sha(content)}, "commit": {"sha": branch["sha"], "message": body.get("message")}}


@app.get("/github/repos/{owner}/{repo}/git/ref/{ref:path}")
def gh_get_ref(owner: str, repo: str, ref: str):
    with world.lock:
        name = ref.removeprefix("heads/")
        if not _repo_ok(owner, repo) or name not in world.branches:
            return _gh_error(404, "Not Found")
        return {"ref": f"refs/heads/{name}", "object": {"sha": world.branches[name]["sha"], "type": "commit"}}


@app.post("/github/repos/{owner}/{repo}/git/refs")
async def gh_create_ref(owner: str, repo: str, request: Request):
    body = await request.json()
    name = str(body.get("ref", "")).removeprefix("refs/heads/")
    with world.lock:
        if not _repo_ok(owner, repo):
            return _gh_error(404, "Not Found")
        if name in world.branches:
            return _gh_error(422, "Reference already exists")
        source = next((b for b in world.branches.values() if b["sha"] == body.get("sha")), None)
        if not source:
            return _gh_error(422, "Object does not exist")
        world.branches[name] = {"files": dict(source["files"]), "sha": source["sha"]}
        return JSONResponse({"ref": f"refs/heads/{name}", "object": {"sha": source["sha"]}}, status_code=201)


@app.get("/github/repos/{owner}/{repo}/pulls")
def gh_list_pulls(owner: str, repo: str, head: str = "", base: str = "", state: str = "open"):
    with world.lock:
        if not _repo_ok(owner, repo):
            return _gh_error(404, "Not Found")
        branch = head.split(":", 1)[-1]
        return [_pull_json(p) for p in world.pulls if (not branch or p["head"] == branch) and (not base or p["base"] == base)
                and (state == "all" or p["state"] == state)]


@app.post("/github/repos/{owner}/{repo}/pulls")
async def gh_create_pull(owner: str, repo: str, request: Request):
    body = await request.json()
    with world.lock:
        if not _repo_ok(owner, repo) or body.get("head") not in world.branches or body.get("base") not in world.branches:
            return _gh_error(422, "Validation Failed: head or base branch does not exist")
        number = len(world.pulls) + 1
        pr = {"number": number, "title": body.get("title", ""), "body": body.get("body", ""), "state": "open",
              "head": body["head"], "base": body["base"], "merged_at": None, "created_at": _now_iso(),
              "head_sha": world.branches[body["head"]]["sha"],
              "html_url": f"{PUBLIC_URL}/github/{REPOSITORY}/pull/{number}"}
        world.pulls.append(pr)
        return JSONResponse(_pull_json(pr), status_code=201)


@app.get("/github/repos/{owner}/{repo}/pulls/{number}")
def gh_get_pull(owner: str, repo: str, number: int):
    with world.lock:
        pr = _find_pull(owner, repo, number)
        return _pull_json(pr) if pr else _gh_error(404, "Not Found")


def _find_pull(owner: str, repo: str, number: int) -> dict[str, Any] | None:
    return next((p for p in world.pulls if p["number"] == number), None) if _repo_ok(owner, repo) else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _changed_files(pr: dict[str, Any]) -> dict[str, tuple[str, str]]:
    head, base = world.branches[pr["head"]]["files"], pr.get("base_snapshot") or world.branches[pr["base"]]["files"]
    return {path: (base.get(path, ""), content) for path, content in head.items() if base.get(path) != content}


def _merge(pr: dict[str, Any]) -> None:
    """What a human reviewer does on GitHub. The control plane has no code path that calls this."""
    main = world.branches[pr["base"]]
    if pr["state"] != "open":
        return
    if world._live is None:
        world._live = dict(main["files"])  # Argo keeps serving the old revision until it syncs.
    pr["base_snapshot"] = dict(main["files"])
    for path, (_, content) in _changed_files(pr).items():
        main["files"][path] = content
    main["sha"] = _sha(main["sha"] + str(pr["number"]))
    pr.update({"state": "closed", "merged_at": _now_iso()})
    world.pending_sync = time.time() + SYNC_DELAY_SECONDS


@app.post("/sim/pulls/{number}/merge")
def sim_merge(number: int) -> dict[str, Any]:
    with world.lock:
        pr = _find_pull(*REPOSITORY.split("/"), number)
        if not pr:
            raise HTTPException(404, "pull request not found")
        _merge(pr)
    return sim_state()


@app.post("/sim/pulls/{number}/close")
def sim_close(number: int) -> dict[str, Any]:
    with world.lock:
        pr = _find_pull(*REPOSITORY.split("/"), number)
        if not pr:
            raise HTTPException(404, "pull request not found")
        if pr["state"] == "open":
            pr["state"] = "closed"
    return sim_state()


def _diff_html(before: str, after: str) -> str:
    import difflib
    rows = []
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=3):
        if line.startswith(("---", "+++")):
            continue
        cls = "add" if line.startswith("+") else "del" if line.startswith("-") else "hunk" if line.startswith("@@") else ""
        rows.append(f'<div class="l {cls}">{html.escape(line) or "&nbsp;"}</div>')
    return "".join(rows)


@app.get("/github/{owner}/{repo}/pull/{number}", response_class=HTMLResponse)
def gh_pull_page(owner: str, repo: str, number: int):
    with world.lock:
        pr = _find_pull(owner, repo, number)
        if not pr:
            raise HTTPException(404, "pull request not found")
        files = "".join(f'<section><h3>{html.escape(path)}</h3><div class="diff">{_diff_html(a, b)}</div></section>'
                        for path, (a, b) in _changed_files(pr).items())
        state = "Merged" if pr["merged_at"] else pr["state"].capitalize()
        actions = (f'<form method="post" action="{PUBLIC_URL}/github/{REPOSITORY}/pull/{number}/merge"><button class="merge">Merge pull request</button></form>'
                   f'<form method="post" action="{PUBLIC_URL}/github/{REPOSITORY}/pull/{number}/close"><button class="close">Close without merging</button></form>'
                   if pr["state"] == "open" else "<p class='done'>This pull request is closed. Return to the control room and press <b>Refresh status</b>.</p>")
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PR #{number} · simulated GitHub</title><style>
body{{margin:0;background:#0b0f17;color:#dbe4f0;font:15px/1.55 ui-sans-serif,system-ui,sans-serif}}main{{max-width:920px;margin:0 auto;padding:32px 20px 80px}}
.banner{{background:#3a2a05;border:1px solid #7a5a12;color:#ffd98a;padding:10px 14px;border-radius:10px;font-size:13px}}
h1{{font-size:24px;margin:22px 0 6px}}h3{{font:600 13px ui-monospace,monospace;margin:0;padding:10px 14px;background:#131a26;border-bottom:1px solid #222c3d}}
.state{{display:inline-block;padding:3px 12px;border-radius:99px;background:{'#5b3fc4' if pr['merged_at'] else '#1f7a45' if pr['state'] == 'open' else '#8a2d2d'};color:#fff;font-size:13px;font-weight:600}}
pre.body{{white-space:pre-wrap;background:#111723;border:1px solid #222c3d;border-radius:10px;padding:14px;font:13px/1.6 ui-sans-serif,system-ui}}
section{{border:1px solid #222c3d;border-radius:10px;overflow:hidden;margin:16px 0}}.diff{{font:12.5px/1.6 ui-monospace,monospace;overflow-x:auto}}
.l{{padding:0 14px;white-space:pre}}.add{{background:#12301f;color:#8fe3ac}}.del{{background:#3a1717;color:#ffa3a3}}.hunk{{color:#7f93b3;background:#141c2b}}
.actions{{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}}button{{font:600 14px ui-sans-serif,system-ui;border:0;border-radius:9px;padding:11px 18px;cursor:pointer;color:#fff}}
.merge{{background:#238636}}.close{{background:#30394a}}.done{{color:#9fb0c8}}small{{color:#8496b0}}</style></head><body><main>
<div class="banner"><b>Simulated GitHub.</b> This page stands in for the human reviewer step on github.com. Nothing here is a real repository.</div>
<h1>{html.escape(pr['title'])} <small>#{number}</small></h1><span class="state">{state}</span> <small>{html.escape(pr['head'])} → {html.escape(pr['base'])} · {REPOSITORY}</small>
<pre class="body">{html.escape(pr['body'])}</pre>{files or '<p>No file changes.</p>'}<div class="actions">{actions}</div></main></body></html>"""


@app.post("/github/{owner}/{repo}/pull/{number}/{action}")
def gh_pull_action(owner: str, repo: str, number: int, action: str):
    with world.lock:
        pr = _find_pull(owner, repo, number)
        if not pr or action not in {"merge", "close"}:
            raise HTTPException(404, "pull request not found")
        if action == "merge":
            _merge(pr)
        elif pr["state"] == "open":
            pr["state"] = "closed"
    return RedirectResponse(f"{PUBLIC_URL}/github/{REPOSITORY}/pull/{number}", status_code=303)


# ============================ stub chat-completions ===========================
def _stub_diagnoser(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = payload.get("evidence", [])
    telemetry = [e for e in evidence if e.get("source") in {"prometheus", "loki", "kubernetes"} and not e.get("error")]
    ids = [e["id"] for e in telemetry if e.get("source") in {"prometheus", "loki"}]
    text = json.dumps(telemetry)
    if "panic:" in text:
        return {"root_cause": "The container panics with an index-out-of-range error in main.firstItem when a request carries no items, then crash-loops.",
                "fault_class": "CODE", "confidence": 0.9, "evidence_ids": ids, "contradictions": [],
                "missing_evidence": ["distributed traces"], "rationale": "Loki shows a Go panic with a stack trace in application code; Prometheus shows CrashLoopBackOff with exit reason Error, not OOMKilled."}
    if "OOMKilled" in text or "memory limit exceeded" in text:
        return {"root_cause": "The single replica is OOM-killed while buffering a request backlog; one replica cannot absorb current load.",
                "fault_class": "INFRA", "confidence": 0.86, "evidence_ids": ids, "contradictions": [],
                "missing_evidence": ["request-rate metric"], "rationale": "Last termination reason is OOMKilled and logs show queue saturation with replicas=1; no application error is logged."}
    if "CrashLoopBackOff" in text or "connection reset" in text:
        return {"root_cause": "Pods are unavailable but the telemetry does not isolate a cause.", "fault_class": "AMBIGUOUS",
                "confidence": 0.4, "evidence_ids": ids, "contradictions": ["Readiness failures without a termination reason"],
                "missing_evidence": ["container exit reason", "application error logs"], "rationale": "Signals fit both a dependency outage and a resource problem."}
    return {"root_cause": "No anomaly is visible in the collected telemetry.", "fault_class": "UNKNOWN", "confidence": 0.2,
            "evidence_ids": ids, "contradictions": ["The report describes a problem that telemetry does not show"],
            "missing_evidence": ["application-level metrics"], "rationale": "Replicas are available and logs are clean."}


def _stub_decider(payload: dict[str, Any]) -> dict[str, Any]:
    diagnosis = payload.get("diagnosis", {})
    action = {"INFRA": "INFRA_FIX", "CODE": "CODE_FIX"}.get(diagnosis.get("fault_class"), "ESCALATE_HUMAN")
    return {"action": action, "confidence": diagnosis.get("confidence", 0),
            "rationale": {"INFRA_FIX": "The fault is a capacity problem that a bounded replica change can address.",
                          "CODE_FIX": "The fault is in application code; a source patch through a reviewed PR is required.",
                          "ESCALATE_HUMAN": "The diagnosis is not confident or specific enough for an automated proposal."}[action],
            "evidence_ids": diagnosis.get("evidence_ids", [])}


def _stub_engineer(payload: dict[str, Any]) -> dict[str, Any]:
    patches = []
    for item in payload.get("files", []):
        content = item.get("content", "")
        if payload.get("action") == "INFRA_FIX":
            updated = re.sub(r"(?m)^(\s*replicas:\s*)(\d+)\s*$", lambda m: f"{m.group(1)}{min(10, max(3, int(m.group(2)) + 2))}", content, count=1)
        else:
            updated = content.replace(BUGGY_LINE, FIXED_LINES, 1)
        if updated != content:
            patches.append({"path": item["path"], "content": updated})
    if payload.get("action") == "INFRA_FIX":
        return {"patches": patches, "summary": "Scale the Deployment to 3 replicas so load is spread and a single OOM kill no longer removes all capacity.",
                "rollback_plan": "Revert this pull request to restore the previous replica count."}
    return {"patches": patches, "summary": "Guard firstItem against an empty slice so requests without items no longer panic.",
            "rollback_plan": "Revert this pull request; the previous image will crash-loop again on empty requests."}


@app.post("/llm/v1/chat/completions")
async def chat_completions(request: Request) -> dict[str, Any]:
    body = await request.json()
    messages = body.get("messages", [])
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "{}")
    try:
        payload = json.loads(user)
    except ValueError:
        payload = {}
    if "Diagnoser" in system:
        answer = _stub_diagnoser(payload)
    elif "Decider" in system:
        answer = _stub_decider(payload)
    else:
        answer = _stub_engineer(payload)
    content = json.dumps(answer)
    prompt_tokens, completion_tokens = len(user) // 4, len(content) // 4
    return {"id": "chatcmpl-sim-" + _sha(user + str(time.time()))[:12], "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", "rule-based-stub"),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens}}
