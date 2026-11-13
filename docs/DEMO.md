# Demo guide — Phase 2 evaluation

A 6–8 minute walkthrough of the working prototype, plus the partial results to show afterwards.
Everything below runs on one laptop with no cluster, no GitHub token and no model key.

## Before you present (10 minutes)

```powershell
git fetch origin claude/nice-pascal-b3qry1
git checkout claude/nice-pascal-b3qry1
python -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements-lock.txt
.\.venv\Scripts\python scripts\sim.py --fresh
```

Wait for `Simulation lab starting — dashboard: http://127.0.0.1:3000`, then open that address.

- **To demo with a real LLM** (for example Claude Haiku 5.5): in the dashboard open *Simulation lab* → **Model** card, enter `anthropic/claude-haiku-5-5` and your API key, press **Use this model**. The banner then names the model. Re-enter it after every restart. If it misbehaves, press **Back to the stub**.
- Do one full rehearsal, then stop with **Ctrl+C** and start again with `--fresh` so the queue is empty.
- Always stop with Ctrl+C in that terminal. If a restart says the database is in use, an old run is still alive: close its terminal first.
- Keep `results/partial-results.md` open in a second tab (GitHub renders the tables).
- Zoom the browser to 110–125% so the back of the room can read it.
- The first build takes about a minute. Do not start it in front of the evaluators.

## What to say up front (30 seconds)

> "Incident-to-PR turns a runtime incident into a reviewed pull request. Agents diagnose and propose; a deterministic policy gate checks the actual diff; a human approves; GitOps deploys; and recovery is verified over a sustained window. The agents never write to the cluster and can never merge.
> What you'll see runs the real control plane against a **simulation lab** — generated telemetry, a rule-based stand-in for the LLM *(if you entered a real model: "with Claude Haiku 5.5 making the decisions")*, and an in-memory GitHub and Argo CD. That lets me show the whole workflow reliably today. Real-cluster and real-model evaluation is Phase 3."

Saying this first matters: the yellow banner says the same thing, and evaluators trust a demo more when its limits are stated before they have to ask.

## Walkthrough

| # | Do this | Say this |
|---|---|---|
| 1 | **Overview.** Point at the hero, the three guardrails, the fleet grid. | "Twelve Online Boutique deployments, all healthy. These three invariants are enforced in code, not in the prompt." |
| 2 | **Connections & policy** → turn on **Automatic detection** → **Apply**. | "Two modes. Manual: a developer reports an incident. Automatic: we poll Prometheus for replica shortfalls, crash loops and restarts, with a two-poll debounce and deduplication." |
| 3 | **Simulation lab** → target `shippingservice` → **Capacity (infrastructure)**. | "I'm injecting a fault: the single replica gets OOM-killed under load." |
| 4 | Back to **Overview**. Wait about 10 s: the card turns red, then an incident appears. Click it. | "Detected automatically — one incident, no duplicates. Detection alone never changes anything." |
| 5 | **Analyze incident.** Scroll through Evidence, Agent assessment, Deterministic checks, Proposed change. | "Evidence is read-only. The diagnosis must cite evidence IDs or it's downgraded. The decision is one of four actions. The proposal is a one-line diff: replicas 1 → 3. The policy gate parsed that diff — six checks — and still requires a human." |
| 6 | Point out there is **no** PR button yet. Type your name → **Approve proposal** → **Open pull request**. | "Nothing can open a PR without a named approver; it's recorded in the audit trail." |
| 7 | **View PR #1** → on the stand-in GitHub page press **Merge pull request**. Close the tab. | "This page stands in for github.com. Merging is the reviewer's action — the bot has no code path that merges." |
| 8 | Wait ~8 s → **Refresh status** → **Check recovery**. Show the window bar. Move on while it fills (2 minutes here; 15 in the real configuration). | "Argo CD syncs, the service comes back, and now we *don't* declare victory. Every health sample must pass for the whole window — that's how we catch a fix that only masks the problem." |
| 9 | While the window fills: **Report incident** → service `frontend`, text "The storefront feels slow for some users" → **Create** → **Analyze incident**. | "Manual mode. The report is only a symptom; telemetry shows nothing wrong, so the diagnosis is Unknown and the system escalates to a human instead of inventing a fix." |
| 10 | Select the `shippingservice` incident again → **Check recovery** until it shows **Recovery verified** (about 2 minutes after the merge). Open the **Timeline** → **Show model traces**. | "Verified only after the sustained window. Every step, model request and response is in the audit trail." |
| 11 | *Optional, only after step 10.* **Simulation lab** → `cartservice` → **Code bug**. Open the incident that appears → **Analyze incident**. | "A different fault: the logs show a Go panic, so the decision is a code fix and the patch is confined to that service's source directory." |

Do step 11 last: Argo CD health covers the whole application, so a second unresolved fault (correctly) stops the first incident from being verified.

If you are short on time, do steps 1–8 and 10 only.

## Partial results (2 minutes)

Open `results/partial-results.md`. Regenerate it any time with the lab running:

```powershell
.\.venv\Scripts\python scripts\evaluate.py --with-tests
```

What to highlight, and how to phrase it:

1. **Safety results — these are real and model-independent.** The policy gate and guard suites: every bypass attempt (path traversal, workflow edits, image swaps, credentials in patches, replica bounds, an action that contradicts the diagnosis, invented evidence, prompt injection) is blocked. Quote the pass counts from section 4.
2. **Masking — the research question in miniature.** Section 3: a replica patch merged for what is really a code fault. The PR merges, Argo syncs, and the incident is still *not* reported as recovered.
3. **Pipeline behaviour.** Section 1: 14 scenarios, each ending in the intended state, with every proposal stopping at human review. Section 2: detection latency and zero duplicates.

**Phrase section 1 carefully.** Say: "These 14 cases validate the pipeline and its guards end to end. The decisions come from a rule-based stand-in written alongside the synthetic faults, so this is not a diagnostic-accuracy number — measuring that with a real LLM on real cluster faults is Phase 3." Do not present 14/14 as model accuracy; an evaluator who asks one question would find out, and the honest framing is the stronger one.

If you entered a real model in the dashboard and have a few spare minutes before the demo, regenerate the decision matrix with that model's decisions (this resets the simulated cluster, so never run it during the demo):

```powershell
.\.venv\Scripts\python scripts\evaluate.py --quick
```

The report then names that model, and section 1 becomes a real measurement of an LLM on synthetic faults. Read the output before presenting it, and report whatever it shows.

## Likely questions

- **"Is this running on a real cluster?"** Not in this demo. The kind lab scripts (Online Boutique, Prometheus, Loki, Grafana, Argo CD) are in the repo; the live path needs a fork and credentials and is the first Phase 3 task.
- **"Is the AI real?"** The integration is real — LangGraph and LiteLLM call the provider's API. With the stub, that endpoint is a deterministic stand-in so the run is reproducible. With a real model entered in the lab, the diagnosis, decision and patch come from that model; only the telemetry and GitHub are simulated.
- **"What stops the model doing something dangerous?"** Four layers that don't depend on the model: it only sees allowlisted files; the gate checks the real diff (infrastructure changes are limited to one field, replicas 1–10); a human must approve; the bot can only open a PR, and the cluster credential is read-only.
- **"What happens if the model is wrong?"** A wrong action that contradicts the diagnosis is overridden to escalation; a wrong but plausible patch is caught either by the reviewer or by verification — the masking case shows the second.
- **"What's left?"** The fault lab (14 faults on the real cluster), an independent hidden-test evaluator, baselines, authentication and roles, automated rollback, and the final evaluation. The README has a phase-by-phase audit.

## If something goes wrong

- **Page won't load:** check the terminal is still running; restart with `--fresh`.
- **Incident doesn't appear after injecting:** automatic detection is off. Turn it on, or just use **Report incident** (manual mode shows the same workflow).
- **"Evidence is stale" on approve:** press **Re-run analysis**, then approve.
- **Total failure:** show the three screenshots and `results/partial-results.md`, and walk through the same story.
