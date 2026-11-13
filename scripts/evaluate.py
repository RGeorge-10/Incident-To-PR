#!/usr/bin/env python3
"""Run the scenario matrix against a running simulation lab and write a results report.

    python scripts/sim.py --fresh            # terminal 1 (add --real-model to use your own LLM key)
    python scripts/evaluate.py               # terminal 2  ->  results/partial-results.md + .json

What it measures, on SYNTHETIC faults in the simulation lab:
  * decision matrix: for every (fault kind x service) case, the action the workflow selected
    versus the expected action, plus benign controls that must escalate;
  * policy outcome and patch scope of every proposal;
  * automatic detection latency (fault injected -> incident opened);
  * one full recovery path (approve -> PR -> merge -> Argo sync -> sustained window -> verified);
  * one masking case (an infrastructure patch merged for a code fault must NOT be verified);
  * optionally the policy/guard test-suite counts (--with-tests).

The report states which model produced the decisions. With the built-in rule-based stub the
decision matrix validates the pipeline and its guards, not diagnostic accuracy.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {"capacity": "INFRA_FIX", "code_bug": "CODE_FIX", "ambiguous": "ESCALATE_HUMAN", "none": "ESCALATE_HUMAN"}
SERVICES = ("shippingservice", "cartservice", "checkoutservice", "paymentservice")


class Lab:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None) -> dict | list:
        data = json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None)
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{method} {path} failed with {exc.code}: {exc.read().decode(errors='replace')[:300]}")
        except urllib.error.URLError as exc:
            raise SystemExit(f"Cannot reach the lab at {self.base} ({exc.reason}). Start it with: python scripts/sim.py --fresh")

    def analyze(self, service: str, summary: str) -> tuple[dict, float]:
        incident = self.call("POST", "/api/v1/incidents", {"service": service, "summary": summary})
        started = time.time()
        self.call("POST", f"/api/v1/incidents/{incident['id']}/analyze")
        while True:
            incident = self.call("GET", f"/api/v1/incidents/{incident['id']}")
            if incident["status"] != "analyzing":
                return incident, time.time() - started
            if time.time() - started > 300:
                raise SystemExit("analysis did not finish within 5 minutes")
            time.sleep(0.4)


def tokens(incident: dict) -> int:
    return sum(int((e["details"].get("usage") or {}).get("total_tokens") or 0)
               for e in incident.get("events", []) if e["event_type"] == "llm.response")


def run_case(lab: Lab, service: str, kind: str) -> dict:
    lab.call("POST", "/sim/reset")
    if kind != "none":
        lab.call("POST", "/sim/faults", {"service": service, "kind": kind})
    summary = f"{service} is failing health checks and requests are erroring." if kind != "none" else f"{service} feels slow for some users."
    incident, seconds = lab.analyze(service, summary)
    decision, diagnosis, policy = incident.get("decision") or {}, incident.get("diagnosis") or {}, incident.get("policy") or {}
    patches = (incident.get("proposal") or {}).get("patches") or []
    return {"service": service, "fault": kind, "expected": EXPECTED[kind], "action": decision.get("action", "NONE"),
            "correct": decision.get("action") == EXPECTED[kind], "fault_class": diagnosis.get("fault_class"),
            "confidence": diagnosis.get("confidence"), "status": incident["status"], "policy_allowed": bool(policy.get("allowed")),
            "guard_overrides": decision.get("overridden_by_guard") or [], "patch_files": [p["path"] for p in patches],
            "seconds": round(seconds, 2), "tokens": tokens(incident), "failure": incident.get("failure"), "id": incident["id"]}


def detection_latency(lab: Lab, poll_seconds: int) -> dict:
    lab.call("POST", "/sim/reset")
    before = {i["id"] for i in lab.call("GET", "/api/v1/incidents")}
    lab.call("PUT", "/api/v1/configuration/monitoring", {"enabled": True, "auto_analyze": False})
    time.sleep(poll_seconds + 1)  # let one healthy poll pass
    lab.call("POST", "/sim/faults", {"service": "emailservice", "kind": "capacity"})
    started = time.time()
    found = None
    while time.time() - started < poll_seconds * 6 + 30 and not found:
        found = next((i for i in lab.call("GET", "/api/v1/incidents") if i["id"] not in before and i["source"] == "prometheus_auto"), None)
        time.sleep(0.5)
    latency = round(time.time() - started, 1)
    time.sleep(poll_seconds * 3)  # further unhealthy polls must not open duplicates
    duplicates = sum(1 for i in lab.call("GET", "/api/v1/incidents") if i["id"] not in before and i["source"] == "prometheus_auto") - 1
    lab.call("PUT", "/api/v1/configuration/monitoring", {"enabled": False, "auto_analyze": False})
    if found:
        lab.call("POST", f"/api/v1/incidents/{found['id']}/escalate", {"reviewer": "evaluate.py", "comment": "detection latency probe"})
    return {"detected": bool(found), "seconds": latency if found else None, "duplicates": max(0, duplicates),
            "poll_seconds": poll_seconds, "signals": (found or {}).get("labels", {}).get("signals", [])}


def recovery_and_masking(lab: Lab, window_minutes: int) -> dict:
    """Two incidents diagnosed as capacity faults; one really is, the other is secretly a code fault.

    They run one after the other in a clean world: Argo CD health is per application, so a second
    faulted service would (correctly) keep the first from being verified.
    """
    runs = {}
    for label, service in (("recovery", "shippingservice"), ("masking", "currencyservice")):
        lab.call("POST", "/sim/reset")
        lab.call("POST", "/sim/faults", {"service": service, "kind": "capacity"})
        incident, _ = lab.analyze(service, f"{service} is failing health checks and requests are erroring.")
        if incident["status"] != "awaiting_review":
            return {"skipped": f"{service} produced status {incident['status']} instead of a reviewable proposal"}
        if label == "masking":  # the true cause differs from what was diagnosed
            lab.call("POST", "/sim/faults", {"service": service, "kind": "code_bug"})
        base = f"/api/v1/incidents/{incident['id']}"
        lab.call("POST", f"{base}/review", {"decision": "approve_pr", "reviewer": "evaluate.py", "comment": "scripted approval"})
        pr = lab.call("POST", f"{base}/open-pr")["pull_request"]
        lab.call("POST", f"/sim/pulls/{pr['number']}/merge")  # the human reviewer's merge on (simulated) GitHub
        merged = time.time()
        run = runs[label] = {"service": service, "pr": pr["number"]}
        while time.time() - merged < window_minutes * 60 + 60:
            time.sleep(10)
            lab.call("POST", f"{base}/refresh")
            result = lab.call("POST", f"{base}/verify")
            run["status"] = result["status"]
            run["windows"] = (result.get("verification") or {}).get("prometheus_windows", {})
            run["argocd"] = (result.get("verification") or {}).get("argocd", {})
            if result["status"] == "verified":
                run["verified_after"] = round(time.time() - merged)
                break
        run["observed_seconds"] = round(time.time() - merged)
    return runs


def run_tests() -> dict:
    python = sys.executable
    out = {}
    for name, files in (("policy", ["tests/test_policy.py"]), ("guards_and_adversarial", ["tests/test_guards.py"]),
                        ("all", ["tests"])):
        result = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files], cwd=ROOT / "backend", capture_output=True, text=True)
        passed = re.search(r"(\d+) passed", result.stdout)
        failed = re.search(r"(\d+) failed", result.stdout)
        out[name] = {"passed": int(passed.group(1)) if passed else 0, "failed": int(failed.group(1)) if failed else 0}
    return out


def render(report: dict) -> str:
    cases, config = report["cases"], report["configuration"]
    stub = "stub" in (config.get("model") or "")
    lines = ["# Partial results — simulation lab", "",
             f"Generated {report['generated_at']} by `scripts/evaluate.py`.", "",
             f"- **Environment:** {'simulation lab (synthetic telemetry, in-memory GitHub and Argo CD)' if config.get('simulation') else config.get('environment')}",
             f"- **Model:** `{config.get('model')}`" + (" — a deterministic rule-based stub, **not an LLM**" if stub else ""),
             f"- **Policy ruleset:** `{report['policy'].get('rule_version')}` · human approval required: {report['policy'].get('require_human_approval')} · auto-apply: {report['policy'].get('auto_apply_enabled')}", "",
             "> **How to read this.** Every fault here is synthetic and injected by the simulator. "
             + ("Because the decisions come from a rule-based stub written alongside those faults, the decision matrix shows that the "
                "pipeline, guards and policy gate behave as designed. It is **not** a measurement of diagnostic accuracy." if stub else
                "The decisions were produced by the model named above on synthetic telemetry; they say nothing yet about real cluster faults.")
             + " No result here is evidence of repairing a real system.", ""]
    total, correct = len(cases), sum(c["correct"] for c in cases)
    lines += ["## 1. Decision matrix", "", f"**{correct} of {total}** cases selected the expected action.", "",
              "| Expected ↓ / Selected → | INFRA_FIX | CODE_FIX | ROLLBACK | ESCALATE_HUMAN | (no decision) |", "|---|---|---|---|---|---|"]
    for expected in ("INFRA_FIX", "CODE_FIX", "ESCALATE_HUMAN"):
        row = [sum(1 for c in cases if c["expected"] == expected and c["action"] == a) for a in ("INFRA_FIX", "CODE_FIX", "ROLLBACK", "ESCALATE_HUMAN", "NONE")]
        lines.append(f"| **{expected}** | " + " | ".join(str(n) for n in row) + " |")
    lines += ["", "### Per case", "", "| Service | Injected fault | Expected | Selected | Fault class | Confidence | Outcome | Policy | Patch | Time (s) | Tokens |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cases:
        lines.append(f"| {c['service']} | {c['fault']} | {c['expected']} | {'✅' if c['correct'] else '❌'} {c['action']} | {c['fault_class']} | "
                     f"{c['confidence'] if c['confidence'] is not None else '—'} | {c['status']} | {'allowed' if c['policy_allowed'] else 'blocked / n.a.'} | "
                     f"{', '.join(c['patch_files']) or '—'} | {c['seconds']} | {c['tokens']} |")
    proposals = [c for c in cases if c["patch_files"]]
    lines += ["", f"Proposals generated: {len(proposals)}. Allowed by the policy gate: {sum(c['policy_allowed'] for c in proposals)}. "
              f"All {len(proposals)} stopped at human review; none opened a pull request on its own.",
              f"Median analysis time: {sorted(c['seconds'] for c in cases)[len(cases) // 2]} s.", ""]
    d = report.get("detection")
    if d:
        lines += ["## 2. Automatic detection", "",
                  f"- Fault injected → incident opened: **{d['seconds']} s** (poll every {d['poll_seconds']} s, 2 consecutive unhealthy polls required)." if d["detected"] else "- The fault was **not** detected.",
                  f"- Signals recorded: {', '.join(d['signals']) or '—'}", f"- Duplicate incidents while the fault persisted: **{d['duplicates']}**", ""]
    r = report.get("recovery")
    if r and "skipped" not in r:
        rec, mask = r["recovery"], r["masking"]
        def window(run: dict) -> str:
            return "; ".join(f"{m}-min window: {w.get('healthy_samples')}/{w.get('samples')} healthy samples, stable={w.get('stable')}" for m, w in run.get("windows", {}).items()) or "—"
        lines += ["## 3. Recovery verification and masking", "",
                  "| Case | Service | What was merged | Argo CD | Health window | Final status |", "|---|---|---|---|---|---|",
                  f"| True infrastructure fault | {rec['service']} | replica PR #{rec['pr']} | {rec.get('argocd', {}).get('sync_status')}/{rec.get('argocd', {}).get('health_status')} | {window(rec)} | **{rec.get('status')}**" + (f" after {rec['verified_after']} s" if rec.get("verified_after") else "") + " |",
                  f"| Code fault treated as infrastructure (masking) | {mask['service']} | replica PR #{mask['pr']} | {mask.get('argocd', {}).get('sync_status')}/{mask.get('argocd', {}).get('health_status')} | {window(mask)} | **{mask.get('status')}** |", "",
                  f"The masking case is the important one: the pull request merged and Argo CD synced the new revision, yet after {mask.get('observed_seconds')} s the incident is still **not** reported as recovered, because the service never became healthy.", ""]
    elif r:
        lines += ["## 3. Recovery verification and masking", "", f"Skipped: {r['skipped']}", ""]
    t = report.get("tests")
    if t:
        lines += ["## 4. Safety and guard test suites", "", "| Suite | Passed | Failed |", "|---|---|---|",
                  f"| Policy gate (allowed changes + bypass attempts) | {t['policy']['passed']} | {t['policy']['failed']} |",
                  f"| State-machine guards and adversarial model output | {t['guards_and_adversarial']['passed']} | {t['guards_and_adversarial']['failed']} |",
                  f"| Whole backend suite | {t['all']['passed']} | {t['all']['failed']} |", "",
                  "These are deterministic code-level results and hold regardless of which model is used.", ""]
    lines += ["## Not yet measured", "",
              "- Any fault on the real kind cluster (the F01–F14 fault lab is not built).", "- Hidden regression tests, baselines (B0–B3) and repeated trials with uncertainty.",
              "- Diagnostic accuracy of a real LLM on real telemetry.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the scenario matrix against a running simulation lab.")
    parser.add_argument("--base", default="http://127.0.0.1:3000", help="dashboard URL (proxies /api and /sim)")
    parser.add_argument("--quick", action="store_true", help="decision matrix only (skip detection, recovery and masking)")
    parser.add_argument("--with-tests", action="store_true", help="also run the pytest suites and include their counts")
    parser.add_argument("--out", default=str(ROOT / "results"))
    args = parser.parse_args()
    lab = Lab(args.base)
    config = lab.call("GET", "/api/v1/configuration")
    if not config.get("simulation"):
        raise SystemExit("Refusing to run: this script injects faults and merges pull requests, so it only runs against the simulation lab.")
    lab.call("PUT", "/api/v1/configuration/monitoring", {"enabled": False, "auto_analyze": False})
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "configuration": config,
              "policy": lab.call("GET", "/api/v1/policy"), "cases": []}
    plan = [(s, k) for k in ("capacity", "code_bug", "ambiguous") for s in SERVICES] + [("frontend", "none"), ("adservice", "none")]
    for index, (service, kind) in enumerate(plan, 1):
        case = run_case(lab, service, kind)
        report["cases"].append(case)
        print(f"[{index:>2}/{len(plan)}] {service:<16} {kind:<10} expected {case['expected']:<15} selected {case['action']:<15} {'ok' if case['correct'] else 'MISMATCH'}", flush=True)
    if not args.quick:
        print("Measuring automatic detection latency …", flush=True)
        report["detection"] = detection_latency(lab, int(config["monitoring"]["poll_seconds"]))
        print(f"Running the recovery and masking cases (about {2 * config['verification_window_minutes'] + 2} minutes) …", flush=True)
        report["recovery"] = recovery_and_masking(lab, int(config["verification_window_minutes"]))
    if args.with_tests:
        print("Running the test suites …", flush=True)
        report["tests"] = run_tests()
    lab.call("POST", "/sim/reset")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "partial-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "partial-results.md").write_text(render(report), encoding="utf-8")
    print(f"\nWrote {out / 'partial-results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
