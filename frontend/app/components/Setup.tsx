"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { put } from "../lib/api";
import { ago } from "../lib/format";
import type { Configuration, Integrations, Policy } from "../lib/types";
import { Badge, Card, Icon } from "./ui";

type Props = {
  configuration: Configuration | null; integrations: Integrations; policy: Policy | null; busy: boolean;
  run: (message: string, operation: () => Promise<unknown>) => Promise<void>;
};

const NAMES: Record<string, string> = { kubernetes: "Kubernetes", prometheus: "Prometheus", loki: "Loki", github: "GitHub", model: "Model provider", argocd: "Argo CD", evaluator: "Hidden-test evaluator" };

export function Setup({ configuration, integrations, policy, busy, run }: Props) {
  const [codeUrl, setCodeUrl] = useState("");
  const [gitopsUrl, setGitopsUrl] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [autoAnalyze, setAutoAnalyze] = useState(false);
  const reposLoaded = useRef(false);
  const monitorDirty = useRef(false);

  useEffect(() => {
    if (!configuration) return;
    if (!reposLoaded.current) {
      setCodeUrl(configuration.code_repository ? `https://github.com/${configuration.code_repository}` : "");
      setGitopsUrl(configuration.gitops_repository && configuration.gitops_repository !== configuration.code_repository ? `https://github.com/${configuration.gitops_repository}` : "");
      reposLoaded.current = true;
    }
    if (!monitorDirty.current) { setEnabled(configuration.monitoring.enabled); setAutoAnalyze(configuration.monitoring.auto_analyze); }
  }, [configuration]);

  const saveRepositories = (event: FormEvent) => {
    event.preventDefault();
    void run(configuration?.github_token_configured ? "Repositories saved and verified with the server token." : "Repositories saved. Access will be verified once GITHUB_TOKEN is configured.",
      () => put("/api/v1/configuration/repositories", { code_repository_url: codeUrl, gitops_repository_url: gitopsUrl }));
  };
  const saveMonitoring = (event: FormEvent) => {
    event.preventDefault();
    void run(enabled ? (autoAnalyze ? "Automatic detection and analysis are on." : "Automatic detection is on; analysis stays manual.") : "Automatic monitoring stopped.",
      async () => { await put("/api/v1/configuration/monitoring", { enabled, auto_analyze: enabled && autoAnalyze }); monitorDirty.current = false; });
  };

  const argo = integrations.argocd;
  const steps: Array<{ done: boolean; title: string; help: string }> = [
    { done: Boolean(integrations.prometheus?.reachable), title: "Telemetry reachable", help: "Run the lab and port-forwards so Prometheus (and Loki) answer on localhost." },
    { done: Boolean(configuration?.model_configured), title: "Model provider key", help: "Set LLM_MODEL and LLM_API_KEY in .env, then restart the API." },
    { done: Boolean(configuration?.github_token_configured), title: "GitHub token", help: "Fine-grained token with Contents + Pull requests read/write on your fork, as GITHUB_TOKEN in .env." },
    { done: Boolean(configuration?.code_repository), title: "Repositories connected", help: "Paste your Online Boutique fork below. One repository can serve code and GitOps." },
    { done: Boolean(argo?.configured && argo.reachable), title: "Argo CD application", help: "Set GITHUB_GITOPS_REPOSITORY and re-run lab-up so Argo CD deploys from your fork after a merge." },
  ];
  const ready = steps.filter(s => s.done).length;

  return <div className="setup">
    <Card eyebrow="Go-live checklist" title={ready === steps.length ? "Everything is connected" : `${ready} of ${steps.length} prerequisites are ready`} className="span-2"
      action={<Badge tone={ready === steps.length ? "ok" : "warn"}>{ready === steps.length ? "Ready" : "Setup needed"}</Badge>}>
      <ol className="checklist">{steps.map(step => <li key={step.title} className={step.done ? "done" : ""}>
        <span><Icon name={step.done ? "check" : "clock"} size={14} /></span><div><b>{step.title}</b><small>{step.done ? "Ready" : step.help}</small></div>
      </li>)}</ol>
    </Card>

    <Card eyebrow="Repository onboarding" title="Connect your code and GitOps repositories">
      <p className="muted">Paste GitHub links. The server uses its own token to verify access — credentials are never typed into or sent from this page.</p>
      <form className="form" onSubmit={saveRepositories}>
        <label>Application code repository<input required value={codeUrl} onChange={e => setCodeUrl(e.target.value)} placeholder="https://github.com/you/microservices-demo" /></label>
        <label>GitOps repository <em>optional — defaults to the code repository</em><input value={gitopsUrl} onChange={e => setGitopsUrl(e.target.value)} placeholder="https://github.com/you/online-boutique-gitops" /></label>
        <button className="btn primary" disabled={busy}>Save repositories<Icon name="arrow" size={16} /></button>
      </form>
      <p className="fine">Base branch <code>{configuration?.base_branch || "main"}</code> · manifests are read from <code>{policy?.infra_context_files?.join(", ") || "—"}</code></p>
    </Card>

    <Card eyebrow="Automatic detection" title="Monitor the connected runtime"
      action={<Badge tone={configuration?.monitoring.enabled ? "ok" : "idle"} pulse={Boolean(configuration?.monitoring.enabled)}>{configuration?.monitoring.enabled ? "Monitoring" : "Stopped"}</Badge>}>
      <p className="muted">Polls Prometheus every {configuration?.monitoring.poll_seconds ?? 30}s in <code>{configuration?.namespace || "online-boutique"}</code> for unavailable replicas, CrashLoopBackOff and repeated restarts. {configuration?.monitoring.consecutive_polls ?? 2} consecutive unhealthy polls open one deduplicated incident.</p>
      <form className="form" onSubmit={saveMonitoring}>
        <label className="switch"><input type="checkbox" checked={enabled} onChange={e => { monitorDirty.current = true; setEnabled(e.target.checked); }} /><i /><span><b>Automatic detection</b><small>Opens incidents from sustained runtime health signals.</small></span></label>
        <label className={`switch ${!enabled || !configuration?.model_configured ? "disabled" : ""}`}><input type="checkbox" checked={enabled && autoAnalyze} disabled={!enabled || !configuration?.model_configured} onChange={e => { monitorDirty.current = true; setAutoAnalyze(e.target.checked); }} /><i /><span><b>Automatic analysis</b><small>{configuration?.model_configured ? "Runs diagnosis and proposal for each detected incident. It always stops at human review." : "Needs a model key (LLM_API_KEY) first."}</small></span></label>
        <button className="btn primary" disabled={busy}>Apply monitoring settings</button>
      </form>
      {configuration?.monitoring.enabled && <p className="fine">Last poll {configuration.monitoring.last_poll.at ? ago(configuration.monitoring.last_poll.at) : "pending"}{configuration.monitoring.last_poll.ok === false ? ` · failed: ${configuration.monitoring.last_poll.error}` : ` · watching ${configuration.monitoring.last_poll.watched} deployments`}</p>}
    </Card>

    <Card eyebrow="Integrations" title="Connection status">
      <ul className="integrations">{Object.entries(integrations).map(([name, item]) => {
        const tone = item.configured && item.reachable !== false ? "ok" : item.configured ? "bad" : "idle";
        return <li key={name}><span className={`integration-icon tone-${tone}`}>{(NAMES[name] || name).slice(0, 1)}</span>
          <div><b>{NAMES[name] || name}</b><small>{item.reason || item.detail || item.mode || (item.configured ? "Configured" : "Not configured")}{name === "argocd" && item.sync_status ? ` · ${item.sync_status} / ${item.health_status}` : ""}</small></div>
          <Badge tone={tone}>{tone === "ok" ? "Connected" : tone === "bad" ? "Unreachable" : "Not set up"}</Badge></li>;
      })}</ul>
    </Card>

    <Card eyebrow="Safety policy" title="What the agents can and cannot do">
      <ul className="rules">
        <li className="allow"><Icon name="eye" size={16} /><div><b>Read telemetry</b><small>Kubernetes, Prometheus and Loki are read-only evidence sources.</small></div></li>
        <li className="allow"><Icon name="layers" size={16} /><div><b>Propose an infrastructure change</b><small>{policy?.infra_change_scope || "Deployment spec.replicas only"} under <code>{policy?.infra_allowed_paths?.join(", ")}</code></small></div></li>
        <li className="allow"><Icon name="cpu" size={16} /><div><b>Propose a code change</b><small>Only inside <code>{policy?.code_allowed_paths?.join(", ")}</code> · at most {policy?.max_patch_files} files</small></div></li>
        <li className="allow"><Icon name="git" size={16} /><div><b>Open a pull request</b><small>After human approval. Evidence older than {Math.round((policy?.evidence_max_age_seconds ?? 1800) / 60)} minutes must be regenerated first.</small></div></li>
        <li className="deny"><Icon name="lock" size={16} /><div><b>Never merge, never write to the cluster</b><small>{policy?.invariant || "GitHub review and Argo CD control deployment."}</small></div></li>
        <li className="deny"><Icon name="lock" size={16} /><div><b>Never touch protected resources</b><small>CI workflows, secrets, RBAC, network policy, Argo CD and evaluator files are always denied.</small></div></li>
      </ul>
      <p className="fine">Ruleset {policy?.rule_version || "—"} · auto-apply {policy?.auto_apply_enabled ? "ENABLED" : "disabled"} · fails closed when any dependency is unavailable</p>
    </Card>
  </div>;
}
