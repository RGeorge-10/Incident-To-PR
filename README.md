# Incident-to-PR

> **Status (13 Nov 2026):** The Phase 9 *platform path* is implemented and tested: a Next.js control room, a FastAPI/LangGraph control plane, manual and automatic incident intake, a deterministic policy gate, human approval, PR creation, and sustained-recovery verification after a merge and Argo CD sync. A 140-test suite and a browser smoke test exercise that path end to end against a **synthetic simulation lab**. The live path against a real cluster still needs your own GitHub repository/token and model key. Several research-plan exit criteria for Phases 1–9 are **not** met (see the phase audit below). No repair result is claimed.

This README describes both the implemented system and the remaining setup needed for a cloneable live run. The application never merges a PR and never writes to Kubernetes. It proposes; the policy gate decides; human review and repository branch protection control merge; Argo CD reconciles only after merge.

## Try it in two minutes (simulation lab)

No cluster, GitHub token or model key is needed. Requires Python 3.11+ and Node.js 22+.

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements-lock.txt      # Windows: .venv\Scripts\pip
.venv/bin/python scripts/sim.py --fresh                     # Windows: .venv\Scripts\python
```

Open `http://127.0.0.1:3000`. The launcher starts three local processes: the simulator (`backend/simulator`, port 9900), the real control-plane API (port 8000) and the dashboard (port 3000). The simulator imitates Prometheus, Loki, the GitHub REST API, the Argo CD API and an OpenAI-compatible chat endpoint, so the unmodified application code runs its whole workflow against it.

- **Automatic mode:** in *Connections & policy* turn on automatic detection, then inject a fault in *Simulation lab*. After two consecutive unhealthy polls one incident appears.
- **Manual mode:** press *Report incident*, then *Analyze incident*.
- Approve the proposal, open the pull request, merge it on the stand-in GitHub page (that is the human reviewer's step), press *Refresh status*, then *Check recovery* until the 2-minute window passes.

**Everything in the simulation is synthetic.** Telemetry is generated, the default "model" is a deterministic rule-based stub, and pull requests exist only in memory. It proves that the workflow, guards and UI function; it is not evidence that real faults are diagnosed or repaired. The verification floors (15 minutes, 10 samples) can only be lowered when `APP_ENV=simulation`.

### Use a real LLM in the simulation lab

The lab can take its diagnosis, decision and patch from a real model while telemetry, GitHub and Argo CD stay simulated. Incident text, synthetic logs and synthetic source files are then sent to that provider.

- **In the dashboard (simplest):** *Simulation lab* → **Model** card → enter a LiteLLM model id (for example `anthropic/claude-haiku-5-5`) and your API key → **Use this model**. The key is checked with one small request, kept in the API's memory only (never written to disk or the audit trail), and forgotten when the lab stops. This endpoint exists only when `APP_ENV=simulation`.
- **From `.env`:** set `LLM_MODEL` and `LLM_API_KEY`, then start with `python scripts/sim.py --fresh --real-model`.

Claude models are supported through LiteLLM's `anthropic/` prefix. Sampling temperature is omitted for them automatically (current Claude models reject it); set `LLM_TEMPERATURE` to override for other providers. `LLM_MAX_TOKENS` defaults to 16000 because thinking tokens count against it.

### Run it in the browser with GitHub Codespaces

`.devcontainer/devcontainer.json` installs the dependencies and starts the lab when a codespace is created on this branch; open the forwarded port 3000. This configuration has not been exercised in Codespaces yet.

## Demo guide and partial results

- [`docs/DEMO.md`](docs/DEMO.md) — a 6–8 minute walkthrough with what to click and say, likely questions, and fallbacks.
- [`results/partial-results.md`](results/partial-results.md) — the latest generated report: decision matrix over 14 synthetic scenarios, automatic detection latency, one verified recovery, one masking case that is correctly not verified, and the policy/guard test counts. The report names the model that produced the decisions; with the stub it validates the pipeline and guards, **not** diagnostic accuracy.

Regenerate it with the lab running:

```bash
python scripts/evaluate.py --with-tests     # full run, about 8 minutes
python scripts/evaluate.py --quick          # decision matrix only
```

## Tests

```bash
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend && ../.venv/bin/python -m pytest -q             # 140 tests, about 30 s
cd frontend && npm ci && npm run typecheck && npm run build
node frontend/e2e/smoke.mjs                                 # browser flow; needs the simulation lab running and Playwright
```

| Suite | What it covers |
|---|---|
| `tests/test_policy.py` | The policy gate: allowed changes, replica bounds/types, every non-replica field, multi-document and invalid YAML, path traversal, protected paths, credentials in patches, size limits, untrusted service names. |
| `tests/test_manual_flow.py` | Manual mode end to end through the real API, LangGraph, LiteLLM and GitHub client: infrastructure fault to verified recovery, code fault, escalation, rejection, closed PR, idempotent PR creation, an unhealthy sample inside the window, and an infrastructure patch that masks a code fault (never verified). |
| `tests/test_monitor.py` | Automatic mode: off by default, two-poll debounce, deduplication, one incident per service, recovery notes, automatic analysis stopping at human review, Prometheus outage, open-incident limit. |
| `tests/test_guards.py` | State-machine guards, stale evidence, restart recovery, Alertmanager token/dedup/resolved handling, and adversarial model output (contradictory or unknown actions, invented evidence, out-of-scope patches, prompt injection, credential-shaped context, provider outage). |
| `tests/test_github_client.py` | Branch-only pushes, resuming a half-finished attempt, refusing foreign branches and stale base revisions. |
| `frontend/e2e/smoke.mjs` | The dashboard in a real browser: both modes, approval gating, simulated merge, verification, narrow-viewport layout. |

CI (`.github/workflows/ci.yml`) runs the backend suite and the frontend typecheck/build on every pull request.

## Phase audit — what "done through Phase 9" does and does not mean

| Phase | State | Gap against the plan's exit criterion |
|---|---|---|
| 0 Project contract | Partial | Scope and action boundary are documented here; no separately versioned contract, owners or threat-model document. |
| 1 Repositories and foundation | Partial | Lockfiles, sample config and CI exist. Branch protection, secret scanning and the separate fork/GitOps repositories are yours to create. |
| 2 Platform and GitOps | Implemented, not re-verified here | Lab scripts are PowerShell + kind; they were not executed in this revision. Deterministic reset and operator stop/pause-sync are not implemented. |
| 3 Fault lab and ground truth | **Not implemented** | No F01–F14 injectors against the real cluster. The simulator's three synthetic faults are test fixtures, not the fault lab. |
| 4 Evaluator and preregistration | **Not implemented** | No hidden tests, mutation testing or preregistration. |
| 5 Data model, API, UI, audit | Mostly | Incident lifecycle, audit trail and UI exist. **No authentication or roles**: the API trusts anything that can reach it, so keep it on loopback. |
| 6 Observation and diagnosis | Implemented | Kubernetes-level signals only (replicas, crash loops, restarts). No traces, no application-level latency/error detection. |
| 7 Proposal-only engineers | Implemented | Rollback proposals are not generated; rollback is escalated to a human. |
| 8 Policy gate, PR, CI | Implemented | The gate and its negative suite exist. Trusted CI and branch protection on the *target* repository are your configuration. |
| 9 Apply, verify, rollback | Partial | Sustained-window verification exists. No automated rollback, no canary, no hidden-test check. |

## Do I need to fork Online Boutique?

Yes, for the live path. The bot pushes a branch and opens a pull request, so it needs a repository your token can write to; it cannot do that on `GoogleCloudPlatform/microservices-demo`.

1. On GitHub, open `GoogleCloudPlatform/microservices-demo` and press **Fork** (keep the name `microservices-demo`).
2. From this repository, run the setup script with your fork's name. It uses the git login already on your computer, adds `deploy/online-boutique/` to the fork in one commit, and writes the repository name into `.env`:

   ```powershell
   .\.venv\Scripts\python scripts\setup-fork.py YOUR_USER/microservices-demo
   ```

   Add `--pin` on a fresh fork to move its `main` to the `v0.10.7` tag the lab runs (this force-pushes the fork's `main`). `--dry-run` shows what would happen without pushing.
3. Create a fine-grained token (GitHub → Settings → Developer settings → Fine-grained tokens) limited to **only that fork**, with **Contents: read and write** and **Pull requests: read and write**. Paste it into `.env` as `GITHUB_TOKEN=`. Set `LLM_MODEL` and `LLM_API_KEY` there too. Never commit `.env` or paste these values into a chat.
4. Run `.\scripts\lab-up.ps1` so Argo CD deploys from the fork, then `.\scripts\port-forward.ps1` and `.\scripts\dev.ps1`.
5. Protect `main` on the fork (pull request required).

One fork serves as both the code and the GitOps repository. The source files offered to the model for each service are listed in `lab/service-context.json` (verified against `v0.10.7`), so `CODE_CONTEXT_FILES` needs no editing for Online Boutique.

**A limit to know before you test a code fix:** the lab runs Google's prebuilt `v0.10.7` images. A merged *infrastructure* PR (replica count) is deployed by Argo CD and can be verified. A merged *code* PR changes source in your fork but nothing rebuilds or redeploys an image, so the running service does not change and verification will not pass. Closing that loop needs an image build pipeline in the fork, which is not part of this prototype.

## Current implementation and live demo

Manual and opt-in automatic incident intake are implemented with safe failure states. Automatic monitoring covers Kubernetes deployment replica shortfalls, CrashLoopBackOff, and repeated container restarts. Application-level latency and error detection still needs service-specific metrics or alert rules. The repo is a research prototype, not a production remediation service.

### Local components

| Component | Version/configuration |
|---|---|
| Python | 3.11.9 |
| Node.js / npm | 24.1.0 / 11.3.0 |
| kind / node image | kind 0.33.0 / Kubernetes 1.37.0 pinned by digest |
| kubectl / Helm | 1.37.0 / 4.3.1 |
| Online Boutique | GoogleCloudPlatform/microservices-demo v0.10.7 (vendored with upstream license) |
| Prometheus chart / kube-state-metrics | 29.47.2 / 9.0.1 |
| Loki / Alloy / Grafana charts | 7.6.0 / 1.15.0 / 10.5.15 |
| Argo CD | v3.6.1 |
| API / UI | FastAPI + LangGraph + LiteLLM / Next.js 16 + React 19 |

Versions for the target, monitoring, logging, Argo CD, and the kind node image are pinned in the lab manifests/scripts. Python ranges are listed in `backend/requirements.txt` and the current resolved set is pinned in `backend/requirements-lock.txt`; npm is pinned by `frontend/package-lock.json`.

### Run it on this machine or a fresh clone

Run PowerShell 7.6 or later from the repository root. Docker Desktop must be installed and running in Linux-container mode. `kind`, `kubectl`, Helm, Python 3.11+, Node.js 24+, npm, and Git must be on `PATH`. The bootstrap script reports their versions. On this machine, Docker Desktop and the pinned Kubernetes tools are installed and the lab cluster is already running.

1. Install application dependencies and create a local-only `.env` if it does not exist:

   ```powershell
   .\scripts\bootstrap.ps1
   ```

2. Edit `.env` before installing the Argo application. Set:

   - `LLM_MODEL` to the exact provider model identifier you intend to use, `LLM_API_KEY` to its credential, and optionally `LLM_FALLBACK_MODEL` / `LLM_API_BASE`. Do not paste keys into source files or commit `.env`.
   - `GITHUB_TOKEN` to a fine-grained token for the target repositories with **Contents: read/write** and **Pull requests: read/write**. The app creates branches and pull requests; it does not merge.
   - `GITHUB_GITOPS_REPOSITORY=OWNER/REPO` for Argo CD's deployment source. Its `deploy/online-boutique` directory must contain a valid `kustomization.yaml` and the configured manifest file. If it is private, set `GITHUB_ARGOCD_READ_TOKEN` to a separate read-only token with repository Contents: read.
   - `CODE_CONTEXT_FILES` and `INFRA_CONTEXT_FILES` to files that exist in the corresponding repositories. `$service` expands to the incident's service name, so `deploy/online-boutique/$service.yaml` selects that service's manifest. Online Boutique services are written in different languages; list each service's real entry files. Files that do not exist are skipped, and a proposal is refused if none exist.
   - `CODE_ALLOWED_PATHS`, `INFRA_ALLOWED_PATHS`, and `PROMETHEUS_HEALTH_QUERY` to the target repository and deployment names. Infrastructure edits are currently limited to changing one YAML Deployment's `spec.replicas` to an integer from 1 through 10.

   `.env.example` has local defaults for the included Online Boutique lab. GitHub and model credentials are blank by design. The configured model provider receives incident details, telemetry evidence, and allowlisted source-file context used to propose a change; use only a provider approved for that data. The UI stays available without credentials; analysis fails closed until a model and repositories are configured.

3. Create the isolated local cluster and install the pinned workload and platform services:

   ```powershell
   .\scripts\lab-up.ps1
   ```

   The script targets only `kind-incident-to-pr`. It installs Online Boutique in `online-boutique`, Prometheus and kube-state-metrics, Loki and Alloy log collection, Grafana, Argo CD, and namespace-scoped observer RBAC. It creates the `online-boutique` Argo CD Application from `GITHUB_GITOPS_REPOSITORY` in `.env`, or from the GitOps URL saved in the app's local SQLite configuration. A read-only Argo repository secret is created only when `GITHUB_ARGOCD_READ_TOKEN` is set. Argo is configured to sync after a Git change with pruning disabled.

4. Start localhost-only forwards and the application:

   ```powershell
   .\scripts\port-forward.ps1
   .\scripts\dev.ps1
   ```

   Open `http://127.0.0.1:3000`. The dashboard proxies `/api` to the API server-side, so the browser needs no CORS setup. The API documentation is at `http://127.0.0.1:8000/docs`, Prometheus at `http://127.0.0.1:9090`, Grafana at `http://127.0.0.1:3001` (lab login `admin` / `incident-to-pr-local`), Argo CD at `https://127.0.0.1:8081`, and Loki's API at `http://127.0.0.1:3100`. These listeners bind to loopback; do not expose the local lab to the public internet. Argo CD uses its self-signed local certificate.

5. In **Connections & policy → Repository onboarding**, paste the HTTPS GitHub URL for the code repository and save. A separate GitOps repository is optional; if omitted, the code repository is used for both roles. Links are stored locally in SQLite. The server uses `GITHUB_TOKEN`; the browser never receives that token. The UI can verify access only after that token is configured. The code repo link saved in the UI overrides `GITHUB_CODE_REPOSITORY`.

6. Choose an operating mode in **Connections & policy → Automatic detection**:

   - **Manual mode:** leave automatic monitoring stopped. Press **Report incident**, enter the service/deployment name and observed symptom, then click **Analyze incident**. Analysis runs in the background and the page updates as each stage finishes. A diagnosis that cites no collected telemetry is downgraded and escalated.
   - **Automatic detection:** enable monitoring. It polls Prometheus every 30 seconds for replica shortfalls, `CrashLoopBackOff`, and more than three container restarts in five minutes. Two consecutive unhealthy polls create one deduplicated incident; recovery appears in its timeline.
   - **Automatic analysis:** optionally enable it after setting the model key. Each detected incident then runs the same evidence, diagnosis, proposal, and policy workflow. GitHub access and repository context are additionally needed to prepare a usable code patch or open a PR. Human approval is still required before a PR can be opened.

   A repository URL alone cannot reveal a runtime issue. The repository must correspond to a workload running in the monitored Kubernetes namespace, and Prometheus must have kube-state-metrics data for it. The automatic detector does not scan source code or identify arbitrary app-level errors; those require matching metrics or alert rules.

   For automatic post-merge deployment, also set `GITHUB_GITOPS_REPOSITORY` in `.env` and run `lab-up.ps1`; saving a GitOps link in the browser does not create an Argo CD Application. Private Argo reads additionally need `GITHUB_ARGOCD_READ_TOKEN`.

7. Inspect the evidence, diagnosis, proposed diff, and policy checks. All patches need human approval, and approval is refused once the evidence is older than `EVIDENCE_MAX_AGE_SECONDS` (re-run the analysis). Rejecting ends the path; **Escalate** hands any open incident to a human. Approving unlocks **Open pull request**; the app pushes a uniquely named branch and creates a PR in the code or GitOps repository selected by the decision. Configure repository branch protection separately so required CI and human review are enforced by GitHub.

8. GitHub Actions and repository reviewers decide whether the change can merge. After an authorized merge, Argo CD syncs the GitOps repository. In the incident UI, refresh the PR, then request verification. Verification requires Argo to report **Synced + Healthy** and Prometheus to report every sample healthy over both 5-minute and 15-minute windows, with at least 20 samples in the long window.

9. Stop local application and port-forward processes with:

   ```powershell
   .\scripts\dev-stop.ps1
   .\scripts\port-forward-stop.ps1
   ```

   The kind cluster is left intact so the demo state and data remain available. To remove it explicitly, run `kind delete cluster --name incident-to-pr` only after you are done with the lab; this also deletes its ephemeral Prometheus, Loki, and Grafana data. Do not delete any other cluster.

### Present implementation boundaries

- Manual incidents, opt-in Prometheus monitoring, and authenticated Alertmanager intake are supported. External alert routing is not provisioned by default.
- Repository URLs can be saved from the web setup panel. Automatic monitoring polls the configured namespace every 30 seconds and requires two consecutive unhealthy readings to create a deduplicated incident. Automatic analysis is separate and optional; it still stops before human approval/PR creation.
- Kubernetes observation uses a freshly minted 10-hour observer token. It can read workload metadata/events in the demo namespace and only `get` the named Argo CD Application; it cannot patch workloads.
- GitHub, model, and Argo-backed completion require the user's own credentials/repository setup. This workspace does not contain those credentials.
- The API has no authentication or role model. Anyone who can reach port 8000 can create, approve and escalate incidents; keep it bound to loopback.
- PR merge is never automatic. `AUTO_APPLY_ENABLED` remains false. Human approval is always required before creating a PR.
- Infrastructure proposals have a strict deterministic one-field policy. Code proposals are constrained to configured source paths and pass through the PR/CI/reviewer path; their correctness is not proven until repository CI and the independent hidden regression check pass.
- Sustained recovery is verified from time-series data, not a one-time check. Automated rollback execution is not enabled; rollback remains a human action.
- The model identifier, redacted-safe exact request messages, response, provider response ID, and usage are recorded in the local incident audit timeline when `LLM_TRACE_ENABLED=true` (the default). Context matching common credential patterns is blocked before it is sent to the model. Audit data and SQLite state remain local and should be treated as private repository data.
- The independent evaluator, mutation testing, held-out fault suite, preregistered statistical analysis, and ORCA/GuardedAct baselines from the research plan remain separate evaluation work. Do not report benchmark generalization or comparative wins from this platform demo.

## 1. Goal and scope

Build a research prototype that detects an incident in a controlled Kubernetes environment, decides whether the response should be an infrastructure fix, code fix, rollback, or human escalation, and proposes any change through a protected GitOps workflow. It must verify recovery and measure when an infrastructure fix only masks a code fault.

**Phase I system under test:** Online Boutique (11 services), a disposable `kind` cluster, GitHub Actions (or equivalent CI), ArgoCD, Prometheus, Loki, Grafana, OpenTelemetry, FastAPI, Next.js, LangGraph, LiteLLM, SQLite, and Langfuse. Confirm and pin actual versions during setup. This is a lab prototype, not a production auto-remediation service.

### Research questions

1. Can telemetry-grounded diagnosis distinguish infrastructure faults from code faults well enough to select the right response class?
2. Can a policy-gated GitOps path produce verified recovery without agents writing directly to the cluster?
3. How often does an infrastructure-only fix mask a code fault, including after a longer observation period?
4. How does the proposed system compare to controlled baselines on the same fault cases?

### Claims boundary

Report results only for the application, faults, models, and environment actually evaluated. The original 14-fault suite plus 5–8 new challenge faults does not establish broad production generalization. Do not claim superiority over ORCA or GuardedAct unless a faithful, comparable implementation is evaluated.

## 2. Developer product workflow

### One-time setup

1. Developer selects the service, source repository, and GitOps repository.
2. Developer connects read-only metrics, logs, traces, and Kubernetes event sources.
3. Developer configures SLO/health indicators, debounce and observation periods, allowed actions, approval requirements, and forbidden paths/resources.
4. Administrator configures repository protections, CI identity, ArgoCD target, model/provider, cost limits, retention, and operator stop control.
5. The app runs read-only connectivity and policy checks. No repair is enabled until required integrations pass.

### Incident workflow

1. Observer detects an SLO/health breach and opens an incident after a 30–60 second initial debounce (tune using development data).
2. Evidence is captured with timestamps and source IDs: metric queries, relevant logs/traces/events, service/version, recent Git changes, and tool errors.
3. Diagnoser returns ranked hypotheses tied to evidence IDs. Unsupported claims are labeled unknown.
4. Decider selects exactly one: `INFRA_FIX`, `CODE_FIX`, `ROLLBACK`, or `ESCALATE_HUMAN`.
5. UI displays symptoms, timeline, evidence, diagnosis, uncertainty, proposed diff/action, risk, checks, approval status, and rollback plan.
6. Independent policy service checks the actual diff. It may allow, require approval, or deny. The agent cannot override it.
7. Trusted CI validates the change. Only protected-branch rules and required approvals can authorize merge.
8. ArgoCD applies the protected revision to the lab target. Agents have no Kubernetes write credential.
9. Independent Verifier checks service health, fault-specific behavior, test results, collateral impact, and recurrence windows.
10. Incident closes as verified recovery, rolled back, denied, failed, or escalated. All decisions/checks/revisions/approvals remain auditable.

## 3. Architecture and trust boundaries

### Components

- **Next.js UI:** service setup, incident queue, evidence timeline, diff/approval, policy settings, deployment status, run history.
- **FastAPI control plane:** authenticated API, event ingestion, incident lifecycle, Git/CI integration.
- **LangGraph orchestrator:** typed state machine; every node has input/output schema, timeout, retry limit, and trace.
- **Agents:** Observer, Diagnoser, Decider, infrastructure Engineer, code Engineer, summarizer. Agents propose; they never authorize or execute writes.
- **Policy service:** deterministic, separately permissioned code evaluates allowlist, bounds, risk, evidence freshness, approvals, concurrency, and attempt limits.
- **Verifier:** independent measurable health/test logic; it does not accept the proposing model’s claim as proof.
- **Records:** SQLite for prototype incident/evaluation records; Langfuse for redacted model traces. Define retention, access, backup, and deletion.
- **Telemetry:** Prometheus, Loki, Grafana, OpenTelemetry for control plane and target.
- **Delivery:** Git provider, trusted CI, ArgoCD; ArgoCD syncs only the protected GitOps branch.
- **Model adapter:** LiteLLM or equivalent; record provider/model/version, settings, prompt/tool versions, responses, token and cost metadata. Fallback-model results are kept separate.

### Repository layout recommendation

1. `incident-to-pr`: control plane, UI, policy service, deployment config, public harness, ordinary tests.
2. `online-boutique`: controlled fork or pinned upstream source for code repairs.
3. `online-boutique-gitops`: desired state and ArgoCD config.
4. Evaluator-only private store for ground truth and hidden tests. Never mount, clone, index, or expose it to agents or agent-controlled CI.

Repo count can differ, but hidden labels/tests must be outside agent read/write and feedback boundaries.

### Mandatory trust rules

- Logs, traces, source comments, issues, and tool output are untrusted data; they may contain prompt injection.
- Agents get narrow read-only tools and restricted egress. No shell with deployment credentials.
- Git bot can create branch/PR only. No admin, ruleset bypass, secret management, or merge rights.
- Gate parses actual diff, not agent prose. Required checks must come from a trusted source.
- Untrusted PR jobs run on disposable workers with no secrets, minimal token permissions, and protected workflow definitions.
- Kubernetes RBAC is least-privilege and namespace-scoped. Only ArgoCD applies approved desired state.
- Missing/stale evidence, conflicting signals, unknown action, unavailable policy/CI/verifier, or timeouts fail closed to stop/escalation.

## 4. Initial repair policy

Use a reviewed, versioned allowlist, not prompt instructions alone.

- **Possible lab auto-actions:** only selected manifest fields in named services, with validated ranges (e.g. resource limits or replica count); images must be from known-good digests/tags.
- **Code:** source paths only; PR only. Require normal tests, build, security/policy checks, deployment validation, and independent hidden regression test in evaluator. Initially human approval before merge.
- **Rollback:** specific known prior revision, affected service identified, and data/schema rollback safety confirmed.
- **Always approval-only or deny:** network policies, secrets, RBAC, namespaces/cluster-scoped objects, service accounts, CI workflows, GitHub rulesets, ArgoCD, policy/evaluator/hidden tests, destructive data operations, or paths outside allowlist.
- **Escalate:** data corruption, ambiguous/multiple plausible causes, weak/conflicting evidence, unknown fault, or out-of-policy action.

For every action document permitted files/fields, bounds, required evidence, preconditions/checks, approval class, rollback, timeout, recurrence signal, and policy tests.

## 5. Step-by-step implementation plan

### Phase 0 — Project contract

1. Confirm scope, research questions, target, deadline, owners, model/cost constraints.
2. Define incident, correct decision, fix success, false remediation, masked failure, violation, recovery, rollback, escalation.
3. Select primary outcomes, invariants, allowed auto-actions, approver roles, and stop conditions.
4. Threat model prompt injection, malicious PR/code/dependency, credential leakage, bad CI result, stale evidence, concurrent incidents, provider outage.
5. Assign owners and commit the project contract.

**Exit:** versioned, unambiguous scope and action boundary.

### Phase 1 — Repositories and development foundation

1. Create repos; pin Online Boutique revision.
2. Protect branches: PR required, trusted required checks, no force push/bot bypass, reviews for policy/CI/evaluator changes.
3. Add lockfiles, formatting/lint/static checks, sample config, secret scanning, dependency/container pinning.
4. Configure safe CI runners with no cluster/deployment secrets.
5. Define API schema versioning, redaction, retention, backup.

**Exit:** clean clone builds control plane/UI and CI passes without local hidden state.

### Phase 2 — Isolated platform and no-agent GitOps

1. Create disposable `kind` cluster and dedicated namespace; document CPU/memory/ports.
2. Install pinned Online Boutique, load generator, Prometheus, Loki, Grafana, OpenTelemetry, ArgoCD.
3. Store desired state in GitOps repo; ArgoCD syncs protected branch only to lab namespace.
4. Create identities/RBAC; prove Observer can read needed state and cannot write; agents cannot access deploy credentials.
5. Implement deterministic clean reset: restore known Git revision, reset workload/data, wait for readiness, generate baseline traffic, verify healthy signals.
6. Add operator stop, pause-sync, rollback. Rehearse manually.

**Exit:** human PR validates/deploys through GitOps and reset/rollback restores known healthy state.

### Phase 3 — Fault lab and ground truth

1. Implement F01–F14: 6 infrastructure, 5 code, 3 ambiguous.
2. For every fault define preconditions, injector, expected behavior, correct decision/fix, service, detection signals, independent test, collateral indicators, reset, timeout.
3. Validate injection/reset without agents; keep labels/ground truth private.
4. Add benign controls and offline prompt-injection cases.
5. Repeat each fault/reset to identify and remove harness flakiness.

**Exit:** every case reproducibly injects, can be detected, and resets to known state.

### Phase 4 — Independent evaluator and preregistration

1. Non-Engineer author writes hidden tests from expected behavior/specification, not injected diff; second reviewer checks validity.
2. Test must fail on faulty build, pass on valid repair, remain non-flaky, and exercise behavior beyond compile/availability.
3. Evaluator alone sees hidden tests. Do not expose detailed outcomes for iterative patch probing; run frozen candidates for confirmatory evaluation.
4. Mutation-test Engineer-authored tests with original fault and plausible mutants; report survivors. This supplements, not replaces, hidden tests.
5. Use variants 1–2 for development and variant 3 as locked test. Independent designer creates 5–8 new challenge faults after agent freeze; specify/hash/seal manifest, fix, labels, tests.
6. Pre-register primary metrics, hypotheses, runs, order/seeds, pairing, exclusions, failures, stopping, analysis, and uncertainty reporting.
7. Power the run count for a stated meaningful effect and budget. Treat fault as a grouping unit; show raw per-fault outcomes. Prioritize proposed system vs B2 on code/ambiguous faults. Do not assume retries are independent faults.
8. Define masked-failure recurrence at 5 and 15–30 minutes; preregister longer windows for slow faults such as leaks.

**Exit:** protocol, evaluator hash, test split, and sample size frozen before confirmatory runs.

### Phase 5 — Data model, API, UI, audit

1. Define versioned schemas for incident, evidence, hypothesis, decision, change, policy, approval, CI, deployment, verification, outcome.
2. Add stable IDs, timestamps, actor/source/version/hash, correlation IDs.
3. Add roles: viewer, approver, operator, admin; enforce authorization server-side.
4. Build service setup, incident list/timeline, evidence, diagnosis, diff/checks, approve/reject/escalate, deployment/verification, policy/run history screens.
5. Mark stale/missing telemetry and uncertainty; never equate confidence with permission.
6. Audit every state transition/tool call; redact secrets and limit raw log exposure.

**Exit:** developer can follow a simulated incident end-to-end without agent write access.

### Phase 6 — Read-only observation and diagnosis

1. Implement health detection and debounce.
2. Add bounded adapters for metrics/logs/traces/events/deploy state/Git changes; sanitize content, retain evidence IDs.
3. Deduplicate incidents and enforce cooldowns.
4. Observer emits incident/evidence record; Diagnoser emits ranked hypotheses, evidence IDs, contradictions, uncertainty, missing evidence, next read-only query.
5. Decider emits exactly one action enum. Weak/conflicting evidence or unknown fault escalates.

**Exit:** dev faults and benign controls create auditable incidents; no target mutation possible.

### Phase 7 — Proposal-only Engineers

1. Infra Engineer gets only approved manifest paths/policy schema and emits minimal diff.
2. Code Engineer gets approved source context/ordinary tests and emits minimal patch/tests; never receives hidden tests/labels.
3. Rollback proposal cites exact prior revision/service.
4. Reject invalid schemas, oversized/unrelated diffs, binary changes, forbidden paths, arbitrary images/commands, policy/test edits.
5. UI presents evidence, diff, risk, checks, approval class, rollback, evidence freshness.

**Exit:** small valid proposals on dev cases; still no merge/apply power.

### Phase 8 — Policy gate, PR, CI

1. Implement deterministic policy with versioned rules, unit/negative tests, fail-closed behavior.
2. Parse actual diff, fields, paths, image digest, permissions, workflows, namespace, evidence freshness, concurrent revision.
3. Enforce allowlist/ranges, approvals, max attempts, idempotency, staleness, and concurrency locks.
4. Bot opens PR only. Trusted CI checks format, unit/regression/integration/security/policy/build/deployment as applicable.
5. Require trusted status checks and approvals; bot cannot bypass or merge.
6. Red-team path traversal, hidden-test access, workflow edit, status spoof, prompt injection, forbidden resource, stale evidence, merge race.

**Exit:** forbidden diffs blocked at repo boundary; allowed diffs validated; bypass attempts logged.

### Phase 9 — Apply, verify, rollback

1. Record baseline health, Git revision, fault ID, expected repair signal.
2. Apply through ArgoCD only, to isolated canary/namespace.
3. Wait with bounded timeout. If control plane is unavailable, stop; no alternate direct write.
4. Verifier checks fault-specific health, independent tests, collateral signals, 5-minute and longer recurrence windows.
5. On harm/non-recovery, rollback if safe, halt retries for incident, preserve evidence, escalate.
6. On recovery, observe through masking period, then close with immutable record.

**Exit:** recovery, denial, CI fail, deployment fail, verifier fail, rollback, and escalation rehearsed.

### Phase 10 — Baselines and shakedown

1. Implement B0 Kubernetes restart, B1 runbooks, B2 infra-only agent, B3 monolithic agent, proposed system.
2. Equalize evidence, tools, time, token/cost budgets, reset, and fault instances; document differences.
3. Run development shakedowns; fix harness issues before freeze.
4. Use 15-category failure taxonomy during dev; double-label sample, adjudicate, freeze definitions.

**Exit:** all systems run from same reset harness and capabilities are documented.

### Phase 11 — Freeze and final evaluation

1. Freeze commits, prompts, policy, model/settings, dependencies, application/GitOps revisions, evaluator hash, test split, protocol.
2. Record hashes. Changes after freeze are versioned deviations, not silent tuning.
3. Execute precomputed randomized/matched runs with reset and audit logs. Apply only preregistered exclusion rules.
4. Run sealed new-fault challenge after freeze; show every case. Run B2 too where feasible under same conditions.
5. Analyze decision confusion, repair/hidden-test success, masking at both windows, attempted/applied violations, collateral harm, recovery time, cost, escalations, rollbacks, failures.
6. Report denominators, per-fault results, assumptions, uncertainty, failures, and limits. Targets are not results.

**Exit:** every reported run traces to immutable system, model, fault, evaluator, and analysis versions.

### Phase 12 — Reproduction and research release

1. Reproduce from clean clone on a second machine using pinned versions.
2. Prepare data dictionary, releasable fault specs, sanitized run data, analysis scripts, figures.
3. Verify bibliography metadata and claims; distinguish reproduced, reimplemented, and literature-only comparisons.
4. Write system/threat model/method/results/failure analysis/limitations/artifact instructions.
5. Archive approved artifacts, hashes, model metadata, run logs under retention policy.

**Exit:** independent reviewer can reproduce evaluation without hidden answers.

## 6. Incident state machine / runbook

Every state has owner, timeout, retry limit, idempotency key, error path, and audit record. No unbounded model loop or duplicate PR loop.

`DETECTED` → `TRIAGING` → `DIAGNOSED` → `DECIDED` → `PROPOSED` → `POLICY_CHECK` → `CI` → `WAITING_APPROVAL` (if required) → `MERGED` → `DEPLOYING` → `VERIFYING` → one terminal state: `RECOVERED`, `ROLLED_BACK`, `DENIED`, `FAILED`, `ESCALATED`.

At each transition, persist input/output and actor. Missing required data transitions to `FAILED` or `ESCALATED`, never to apply. Repeated transient read errors use bounded backoff; writes are never blindly retried.

## 7. Metrics and reporting

- **Decision:** four-way confusion matrix, per-class and per-fault counts.
- **Fix:** service health plus independent regression; code repair only counts when hidden test fails on faulty build and passes on frozen repair.
- **Masking:** apparent infra recovery followed by recurrence; report 5-minute and 15–30-minute outcomes (plus preregistered slow-fault window).
- **Safety:** attempts at forbidden actions, denials, applied violations (hard invariant target zero), bypass suite outcomes, collateral impact, rollback success.
- **Operations:** recovery-time distribution, token/cost, escalation, loop/retry, tool/model failures.
- **Analysis:** state unit and assumptions; show denominators/per-fault values; account for grouping/repeats and unequal run counts. Do not present naive pooled intervals as independent-trial evidence.
- **Failure review:** frozen taxonomy; double-code and adjudicate; separate agent, policy, tool, infrastructure, and harness failure.

## 8. Operational requirements

- Operator kill switch disables new automation and pauses apply path.
- Policy, CI, evaluator, Git, telemetry, or verifier outage blocks merge/apply (fail closed).
- Evidence expires; regenerate diagnosis before approval/apply if stale.
- Concurrent changes to a service serialize or revalidate base revision immediately before merge.
- Retry limits, cooldowns, and incident idempotency prevent loops.
- Secrets never enter prompts, traces, logs, PRs, or source. Use protected secrets; do not expose them to model/test jobs.
- Pin dependencies/container digests; scan and review supply chain changes.
- Provider/model/prompt/tool changes are version changes; rerun smoke/safety suite. Report fallback runs separately.
- Define data access, redaction, retention, deletion, and backup.
- Human reject/escalate/override is logged; never count an unsafe suggestion as safe because a human caught it.

## 9. Phase I definition of done

- Clean clone builds and starts documented lab.
- Fault injectors and resets are repeatable; ground truth is isolated.
- Developer UI covers setup through incident closure and audit.
- Agents lack direct cluster-write access; independent policy and trusted CI enforce boundary.
- Forbidden diffs and prompt-injection/bypass attempts are blocked/logged.
- Hidden evaluator is independent, stable, and not queryable by Engineer.
- Recovery, masking, denial, fail-closed, rollback, and escalation paths are demonstrated.
- Baselines use comparable conditions; differences disclosed.
- Run count, holdout, analysis, exclusions frozen before confirmatory experiments.
- Results and failures reproduce from recorded hashes/versions.
- Paper/README claims only tested capabilities and states limitations.

## 10. Recommended defaults to resolve before coding

| Decision | Phase I default |
|---|---|
| Deployment | Disposable local `kind` only |
| Agent authority | No direct writes; proposal/PR only |
| Initial autonomy | Small set of bounded, reversible infrastructure changes in lab |
| Code fixes | PR, hidden evaluator, human merge approval initially |
| Ambiguous/destructive faults | Escalate; never auto-repair data corruption |
| Branch rules | PR + trusted required checks + no bot bypass; protect CI/policy/evaluator |
| Evidence | Calibrate thresholds on dev data; uncertainty/conflict fails closed |
| Recovery | Independent checks, 5-minute and longer masking windows |
| Model | Pin where possible; log exact metadata; separate fallback results |
| Budget | Measure actual rate limits/cost in week one; don't assume free tier |
| Related systems | Direct comparison only if faithful; otherwise related-work discussion |
| Statistics | Preregister; per-fault results; power plan accounts for repeated trials |

Changing a default requires a recorded rationale before the affected phase is frozen.

## 11. Local setup and command contract

The current executable commands and their real behavior are:

| Entry point | Required behavior |
|---|---|
| `scripts/bootstrap.ps1` | Check tool availability, install the pinned Python lock and npm lock, create local data and `.env.example` copy only when missing, and never fabricate credentials. |
| `scripts/lab-up.ps1` | Create or reuse only `kind-incident-to-pr`; install the vendored Online Boutique and pinned Prometheus, kube-state-metrics, Loki, Alloy, Grafana, and Argo CD resources; apply observer RBAC; optionally create the GitOps Argo Application from `.env`; wait for rollouts. |
| `scripts/observer-kubeconfig.ps1` | Mint a short-lived (10-hour) observer token and write a `.local` kubeconfig. The app receives read-only workload/event access and read-only access to the named Argo Application. |
| `scripts/port-forward.ps1` / `scripts/port-forward-stop.ps1` | Start or stop tracked loopback-only forwards for Prometheus, Loki, Grafana, and Argo CD. Stop checks the saved PID command before terminating it. |
| `scripts/dev.ps1` / `scripts/dev-stop.ps1` | Start or stop the FastAPI and Next.js development processes. Logs and PID records live under ignored `.local/`. |
| `scripts/sim.py` | Cross-platform. Start the synthetic simulation lab (simulator, API, dashboard) with every integration pointed at the simulator; real credentials in `.env` are not used. |
| `scripts/evaluate.py` | Run the scenario matrix against a running simulation lab and write `results/partial-results.md` and `.json`. Refuses to run outside the simulation lab. |
| `scripts/setup-fork.py` | Cross-platform. Add `deploy/online-boutique/` to your Online Boutique fork and record the repository in `.env`. Never handles tokens. |
| `scripts/prepare-gitops.py` | Split the vendored Online Boutique manifest into the per-Deployment GitOps layout in `lab/gitops/online-boutique/`. |

The following research/evaluation tools are **not implemented yet**: independent hidden regression evaluator, mutation-testing harness, held-out fault suite, preregistered analysis runner, sample-size/power calculator, ORCA/GuardedAct baseline adapters, dedicated fault injector/resetter, and research-report generator. The plan in sections 5–10 describes these as future work; do not read them as shipped commands or completed experiments. No automated rollback or auto-merge command exists. Keep all credentials local or in a proper secret store; never commit `.env`, observer tokens, model API keys, or GitHub tokens.

## 12. Primary references

- [Kubernetes RBAC good practices](https://kubernetes.io/docs/concepts/security/rbac-good-practices/)
- [GitHub rulesets and required status checks](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [NIST AI Risk Management Framework 1.0](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf)

