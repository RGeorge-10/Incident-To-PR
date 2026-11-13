"use client";

import { useMemo, useState } from "react";
import { post } from "../lib/api";
import { lineDiff } from "../lib/diff";
import { ago, clock, nice, shortId, SOURCE_LABEL, STAGES, stageOf, stamp, statusOf, type Tone } from "../lib/format";
import type { AuditEvent, Configuration, Evidence, HealthWindow, Incident, Patch } from "../lib/types";
import { Badge, Card, Empty, Icon, Ring } from "./ui";

type Props = {
  incident: Incident; configuration: Configuration | null; busy: boolean;
  run: (message: string, operation: () => Promise<unknown>) => Promise<void>;
};

const ACTION_COPY: Record<string, { label: string; tone: Tone; hint: string }> = {
  INFRA_FIX: { label: "Infrastructure fix", tone: "info", hint: "Bounded manifest change through GitOps" },
  CODE_FIX: { label: "Code fix", tone: "violet", hint: "Source patch through a reviewed PR" },
  ROLLBACK: { label: "Rollback", tone: "warn", hint: "Human-operated in this phase" },
  ESCALATE_HUMAN: { label: "Escalate to human", tone: "warn", hint: "No automated proposal" },
};

export function IncidentDetail({ incident, configuration, busy, run }: Props) {
  const [reviewer, setReviewer] = useState("");
  const [comment, setComment] = useState("");
  const status = statusOf(incident.status);
  const stage = stageOf(incident);
  const base = `/api/v1/incidents/${incident.id}`;
  const modelReady = Boolean(configuration?.model_configured);
  const canAnalyze = ["open", "failed", "policy_denied", "awaiting_review"].includes(incident.status);
  const canEscalate = ["open", "failed", "policy_denied", "awaiting_review", "ready_for_pr", "pr_open", "merged_waiting_verification", "deployed_verifying"].includes(incident.status);
  const pr = incident.pull_request;

  return <div className="detail">
    <section className="card detail-head">
      <div className="detail-title">
        <div>
          <p className="eyebrow">Incident #{shortId(incident.id)} · {SOURCE_LABEL[incident.source] || incident.source} · {stamp(incident.created_at)}</p>
          <h2>{incident.service}</h2>
          <p className="detail-summary">{incident.summary}</p>
        </div>
        <Badge tone={status.tone} pulse={incident.status === "analyzing"}>{status.label}</Badge>
      </div>

      <ol className="journey" aria-label="Incident progress">
        {STAGES.map((name, index) => {
          const state = stage.done || index < stage.index ? "done" : index === stage.index ? (stage.stopped ? "stopped" : "current") : "todo";
          return <li key={name} className={state}>
            <span>{state === "done" ? <Icon name="check" size={14} /> : state === "stopped" ? <Icon name="x" size={14} /> : index + 1}</span>
            <b>{name}</b>
          </li>;
        })}
      </ol>

      <div className={`next tone-${status.tone}`}>
        <NextStep incident={incident} modelReady={modelReady} />
        <div className="next-actions">
          {incident.status === "awaiting_review" && <div className="review-form">
            <input value={reviewer} onChange={e => setReviewer(e.target.value)} placeholder="Your name (recorded in the audit trail)" aria-label="Reviewer name" maxLength={200} />
            <input value={comment} onChange={e => setComment(e.target.value)} placeholder="Review note (optional)" aria-label="Review note" maxLength={2000} />
            <button className="btn primary" disabled={busy || !reviewer.trim()} onClick={() => void run("Approved. You can now open the pull request.", () => post(`${base}/review`, { decision: "approve_pr", reviewer, comment }))}><Icon name="check" size={16} />Approve proposal</button>
            <button className="btn danger" disabled={busy || !reviewer.trim()} onClick={() => void run("Proposal rejected.", () => post(`${base}/review`, { decision: "reject", reviewer, comment }))}>Reject</button>
          </div>}
          {incident.status === "ready_for_pr" && <button className="btn primary" disabled={busy} onClick={() => void run("Pull request opened. Merging stays with GitHub reviewers.", () => post(`${base}/open-pr`))}><Icon name="git" size={16} />Open pull request</button>}
          {pr && incident.status !== "verified" && <>
            <a className="btn ghost" href={pr.url} target="_blank" rel="noreferrer">View PR #{pr.number}<Icon name="external" size={15} /></a>
            <button className="btn ghost" disabled={busy} onClick={() => void run("Pull request and deployment status refreshed.", () => post(`${base}/refresh`))}><Icon name="refresh" size={15} />Refresh status</button>
            {pr.merged && <button className="btn primary" disabled={busy} onClick={() => void run("Recovery check recorded.", () => post(`${base}/verify`))}><Icon name="shield" size={16} />Check recovery</button>}
          </>}
          {canAnalyze && <button className={incident.status === "open" || incident.status === "failed" ? "btn primary" : "btn ghost"} disabled={busy || !modelReady}
            title={modelReady ? undefined : "Set LLM_MODEL and LLM_API_KEY in .env, then restart the API"}
            onClick={() => void run("Analysis started. Evidence, diagnosis and the proposal appear as each stage finishes.", () => post(`${base}/analyze`))}>
            <Icon name="spark" size={16} />{incident.status === "open" ? "Analyze incident" : "Re-run analysis"}</button>}
          {canEscalate && <button className="btn quiet" disabled={busy} onClick={() => {
            const name = reviewer.trim() || window.prompt("Your name (recorded in the audit trail):")?.trim();
            if (name) void run("Incident escalated to a human owner.", () => post(`${base}/escalate`, { reviewer: name, comment }));
          }}><Icon name="flag" size={15} />Escalate</button>}
        </div>
      </div>
      {incident.failure && <p className="callout bad"><Icon name="info" size={16} />{incident.failure}</p>}
    </section>

    <div className="detail-grid">
      <DiagnosisCard incident={incident} />
      <PolicyCard incident={incident} />
    </div>

    {incident.proposal && <ProposalCard incident={incident} />}
    {(pr || incident.verification) && <DeliveryCard incident={incident} configuration={configuration} />}

    <div className="detail-grid">
      <EvidenceCard evidence={incident.evidence || []} analyzing={incident.status === "analyzing"} />
      <Timeline events={incident.events || []} />
    </div>
  </div>;
}

function NextStep({ incident, modelReady }: { incident: Incident; modelReady: boolean }) {
  const copy: Record<string, [string, string]> = {
    open: modelReady ? ["Ready to analyze", "Collects read-only telemetry, then asks the model for a diagnosis, a decision and a policy-checked proposal."]
      : ["Model provider not configured", "Add LLM_MODEL and LLM_API_KEY to .env and restart the API to enable analysis. The incident stays open until then."],
    analyzing: ["Analysis in progress", "Gathering evidence → diagnosing → deciding → proposing → policy gate. This panel updates live."],
    awaiting_review: ["Your review is required", "The policy gate allowed this patch, but nothing happens until a human approves. Approval only lets the bot open a PR — it cannot merge."],
    ready_for_pr: ["Approved — open the pull request", "The bot pushes a uniquely named branch and opens a PR. CI and branch protection on GitHub decide the merge."],
    pr_open: ["Waiting for review on GitHub", "Merge the PR on GitHub after CI and reviewers approve, then refresh the status here."],
    merged_waiting_verification: ["Merged — verifying recovery", "Argo CD must report Synced + Healthy and every health sample must pass for the full observation window."],
    deployed_verifying: ["Deployed — observing for masking", "Initial health looks good. Recovery is only verified after the sustained window completes."],
    verified: ["Recovery verified", "Argo CD is Synced + Healthy and the health signal stayed good for the whole observation window. This record is final."],
    escalated: ["Escalated to a human", "No automated change was proposed. A person owns this incident from here."],
    policy_denied: ["Blocked by the policy gate", "The proposal broke a deterministic rule, so it can never become a PR. Fix the cause and re-run the analysis, or escalate."],
    rejected: ["Closed without a change", "The proposal was rejected or its pull request was closed unmerged."],
    failed: ["Analysis failed safely", "Nothing was proposed or changed. You can re-run the analysis."],
  };
  const [title, body] = copy[incident.status] || [nice(incident.status), ""];
  return <div className="next-copy">{incident.status === "analyzing" && <span className="spinner" aria-hidden="true" />}<div><b>{title}</b><p>{body}</p></div></div>;
}

function DiagnosisCard({ incident }: { incident: Incident }) {
  const diagnosis = incident.diagnosis;
  const action = incident.decision?.action ? ACTION_COPY[incident.decision.action] : undefined;
  const confidence = Number(diagnosis?.confidence || 0);
  return <Card eyebrow="Diagnosis & decision" title="Agent assessment" action={action && <Badge tone={action.tone}>{action.label}</Badge>}>
    {!diagnosis ? <Empty icon="cpu" title={incident.status === "analyzing" ? "Diagnosing…" : "No diagnosis yet"}>The Diagnoser must cite collected evidence IDs. Unsupported conclusions are downgraded to Unknown.</Empty> : <>
      <div className="diagnosis">
        <Ring value={confidence} tone={confidence >= 0.65 ? "ok" : "warn"} label="Model confidence" />
        <div><small className="label">Likely cause · {nice(diagnosis.fault_class)} fault</small><p>{diagnosis.root_cause || "Not provided"}</p></div>
      </div>
      {diagnosis.rationale && <p className="rationale">{diagnosis.rationale}</p>}
      {diagnosis.guard && <p className="callout warn"><Icon name="shield" size={16} />{diagnosis.guard}</p>}
      <dl className="pairs">
        <div><dt>Cited evidence</dt><dd>{diagnosis.evidence_ids?.length ? diagnosis.evidence_ids.map(id => <code key={id}>{id}</code>) : "None"}</dd></div>
        {Boolean(diagnosis.contradictions?.length) && <div><dt>Contradictions</dt><dd>{diagnosis.contradictions!.join(" · ")}</dd></div>}
        {Boolean(diagnosis.missing_evidence?.length) && <div><dt>Missing evidence</dt><dd>{diagnosis.missing_evidence!.join(" · ")}</dd></div>}
      </dl>
      {incident.decision && <div className="decision">
        <small className="label">Decision{action ? ` · ${action.hint}` : ""}</small>
        <p>{incident.decision.rationale || "No rationale provided."}</p>
        {incident.decision.overridden_by_guard?.map(reason => <p className="callout warn" key={reason}><Icon name="shield" size={16} />Deterministic guard overrode the model: {reason}</p>)}
      </div>}
    </>}
  </Card>;
}

function PolicyCard({ incident }: { incident: Incident }) {
  const policy = incident.policy;
  return <Card eyebrow="Policy gate" title="Deterministic checks" action={policy ? <Badge tone={policy.allowed ? "ok" : "bad"}>{policy.allowed ? "Allowed" : "Blocked"}</Badge> : undefined}>
    {!policy ? <Empty icon="shield" title="Not evaluated yet">The gate parses the actual patch — never the model&apos;s description of it — and fails closed.</Empty> : <>
      {Boolean(policy.checks?.length) && <ul className="checks">{policy.checks!.map(check => <li key={check.name} className={check.passed ? "pass" : "fail"}>
        <span><Icon name={check.passed ? "check" : "x"} size={13} /></span><div><b>{check.name}</b>{check.detail && <small>{check.detail}</small>}</div>
      </li>)}</ul>}
      {!policy.checks?.length && policy.reasons?.map(reason => <p className="callout bad" key={reason}><Icon name="x" size={16} />{reason}</p>)}
      {Boolean(policy.checks?.length) && policy.reasons?.filter(r => !policy.checks!.some(c => c.detail?.includes(r))).map(reason => <p className="callout bad" key={reason}><Icon name="x" size={16} />{reason}</p>)}
      <ul className="checks">
        <li className={incident.review?.decision === "approve_pr" ? "pass" : incident.review?.decision === "reject" ? "fail" : "wait"}>
          <span><Icon name={incident.review?.decision === "approve_pr" ? "check" : incident.review?.decision === "reject" ? "x" : "user"} size={13} /></span>
          <div><b>Human approval</b><small>{incident.review ? `${incident.review.decision === "approve_pr" ? "Approved" : "Rejected"} by ${incident.review.reviewer}${incident.review.comment ? ` — “${incident.review.comment}”` : ""}` : "Always required before a pull request can be opened"}</small></div>
        </li>
      </ul>
      <p className="fine">Ruleset {policy.rule_version || "—"}</p>
    </>}
  </Card>;
}

function ProposalCard({ incident }: { incident: Incident }) {
  const proposal = incident.proposal!;
  const patches = proposal.patches || [];
  return <Card eyebrow="Proposed change" title={proposal.summary || "No change was proposed"} className="proposal"
    action={<Badge tone="idle"><Icon name="git" size={13} />{proposal.repository || "No repository"} @ {proposal.base_branch || "main"}</Badge>}>
    {patches.length === 0 ? <Empty icon="layers" title="No patch">The diagnosis is kept, but there is nothing to review.</Empty>
      : patches.map(patch => <DiffView key={patch.path} patch={patch} />)}
    {proposal.rollback_plan && <p className="callout info"><Icon name="refresh" size={16} /><span><b>Rollback plan.</b> {proposal.rollback_plan}</span></p>}
  </Card>;
}

function DiffView({ patch }: { patch: Patch }) {
  const diff = useMemo(() => lineDiff(patch.base_content ?? "", patch.content), [patch.base_content, patch.content]);
  return <figure className="diff">
    <figcaption><Icon name="layers" size={15} /><code>{patch.path}</code><span className="plus">+{diff.added}</span><span className="minus">−{diff.removed}</span></figcaption>
    <div className="diff-body" tabIndex={0}>{diff.lines.map((line, index) => line.kind === "gap"
      ? <div key={index} className="diff-line gap"><span /><span /><code>⋯ {line.text}</code></div>
      : <div key={index} className={`diff-line ${line.kind}`}><span>{line.oldNo ?? ""}</span><span>{line.newNo ?? ""}</span><code>{line.kind === "add" ? "+" : line.kind === "del" ? "−" : " "} {line.text}</code></div>)}</div>
  </figure>;
}

function DeliveryCard({ incident, configuration }: { incident: Incident; configuration: Configuration | null }) {
  const pr = incident.pull_request;
  const verification = incident.verification;
  const argo = verification?.argocd;
  const windows = Object.entries(verification?.prometheus_windows || {});
  return <Card eyebrow="Delivery & verification" title="Pull request, GitOps sync and sustained recovery">
    <div className="delivery">
      <div className="delivery-step">
        <small className="label">Pull request</small>
        {pr ? <><b>#{pr.number} · {pr.merged ? "Merged" : nice(pr.state)}</b><p>{pr.repository}{pr.head_branch ? ` · ${pr.head_branch}` : ""}</p>
          <Badge tone={pr.merged ? "violet" : pr.state === "open" ? "ok" : "idle"}>{pr.merged ? `Merged ${ago(pr.merged_at)}` : pr.state === "open" ? "Awaiting GitHub review" : "Closed"}</Badge></> : <p>Not opened</p>}
      </div>
      <div className="delivery-step">
        <small className="label">Argo CD</small>
        {argo ? <><b>{argo.sync_status || (argo.synced ? "Synced" : "Unknown")} · {argo.health_status || (argo.healthy ? "Healthy" : "Unknown")}</b>
          <p>{argo.reason || (argo.revision ? `Revision ${String(argo.revision).slice(0, 10)}` : "Read-only status")}</p>
          <Badge tone={argo.synced && argo.healthy ? "ok" : argo.configured ? "warn" : "idle"}>{argo.synced && argo.healthy ? "Synced + Healthy" : argo.configured ? "Not converged" : "Not connected"}</Badge></> : <p>Checked after merge</p>}
      </div>
      <div className="delivery-step wide">
        <small className="label">Sustained health · every sample must pass</small>
        {windows.length === 0 ? <p>{pr?.merged ? "Press “Check recovery” to evaluate the observation windows." : `Starts after merge. Window: ${configuration?.verification_window_minutes ?? 15} minutes.`}</p>
          : windows.map(([minutes, window]) => <WindowBar key={minutes} minutes={minutes} window={window} />)}
      </div>
    </div>
    {verification?.checked_at && <p className="fine">Last checked {clock(verification.checked_at)} · observation started {clock(verification.started_at)}</p>}
  </Card>;
}

function WindowBar({ minutes, window }: { minutes: string; window: HealthWindow }) {
  const total = Number(window.window_minutes || minutes) * 60;
  const progress = Math.min(1, Number(window.elapsed_seconds || 0) / total);
  const unhealthy = Number(window.samples || 0) - Number(window.healthy_samples || 0);
  const tone = window.stable ? "ok" : unhealthy > 0 ? "bad" : "violet";
  return <div className={`window tone-${tone}`}>
    <div className="window-top"><b>{minutes}-minute window</b><span>{window.stable ? "Stable" : unhealthy > 0 ? `${unhealthy} unhealthy sample${unhealthy === 1 ? "" : "s"}` : progress < 1 ? `${Math.round(progress * 100)}% elapsed` : window.reason || "Incomplete"}</span></div>
    <div className="window-bar"><span style={{ width: `${progress * 100}%` }} /></div>
    <small>{window.samples ?? 0} samples · {window.healthy_samples ?? 0} healthy · minimum {window.minimum_samples ?? "—"}</small>
  </div>;
}

function EvidenceCard({ evidence, analyzing }: { evidence: Evidence[]; analyzing: boolean }) {
  return <Card eyebrow="Evidence" title="What the system observed" action={<span className="count">{evidence.length}</span>}>
    {evidence.length === 0 ? <Empty icon="eye" title={analyzing ? "Collecting evidence…" : "Nothing collected yet"}>Analysis gathers read-only data from Prometheus, Loki, Kubernetes and the incident report.</Empty>
      : <div className="evidence">{evidence.map((item, index) => {
        const state: Tone = item.error ? "warn" : item.configured ? "ok" : "idle";
        return <details key={String(item.id || index)} open={index === 1 && !item.error}>
          <summary><span className={`source tone-${state}`}>{String(item.source || "?").slice(0, 2).toUpperCase()}</span>
            <span className="evidence-name"><b>{nice(String(item.source || "evidence"))}</b><code>{String(item.id || "")}</code></span>
            <Badge tone={state}>{item.error ? "Unavailable" : item.configured ? "Collected" : "Not configured"}</Badge></summary>
          <EvidenceBody item={item} />
        </details>;
      })}</div>}
  </Card>;
}

function EvidenceBody({ item }: { item: Evidence }) {
  if (item.error) return <p className="callout warn"><Icon name="info" size={16} />{item.error}</p>;
  if (item.source === "loki") {
    const lines = (item.items || []) as Array<{ line?: string; pod?: string }>;
    return lines.length ? <pre className="logs">{lines.map((l, i) => <span key={i} className={/panic|error|fatal|OOM|exceeded/i.test(l.line || "") ? "hot" : ""}>{l.line}{"\n"}</span>)}</pre> : <p className="muted">No log lines in the last 10 minutes.</p>;
  }
  if (item.source === "prometheus") {
    const rows = (item.items || []) as Array<{ name?: string; query?: string; result?: Array<{ metric?: Record<string, string>; value?: [number, string] }> }>;
    return <table className="metrics"><tbody>{rows.map((row, i) => <tr key={i}><th title={row.query}>{nice(row.name)}</th><td>{row.result?.length
      ? row.result.slice(0, 4).map((r, k) => <span key={k}><b>{r.value?.[1]}</b>{r.metric?.reason ? ` ${r.metric.reason}` : r.metric?.pod ? ` ${r.metric.pod}` : ""}</span>) : <em>no series</em>}</td></tr>)}</tbody></table>;
  }
  if (item.source === "incident_report") return <p className="quote">“{String(item.summary || "")}”</p>;
  return <pre className="raw">{JSON.stringify(item, null, 2)}</pre>;
}

const ACTOR_ICON: Array<[RegExp, string, Tone]> = [
  [/^model:/, "cpu", "violet"], [/monitor|observer|alertmanager/, "radar", "info"], [/policy/, "shield", "warn"],
  [/github/, "git", "info"], [/verifier/, "shield", "ok"], [/workflow|system|diagnoser|decider|engineer/, "bolt", "idle"],
];

function Timeline({ events }: { events: AuditEvent[] }) {
  const [showModel, setShowModel] = useState(false);
  const visible = showModel ? events : events.filter(e => !e.event_type.startsWith("llm."));
  const modelCount = events.length - events.filter(e => !e.event_type.startsWith("llm.")).length;
  return <Card eyebrow="Audit trail" title="Timeline" action={modelCount > 0 ? <button className="btn ghost small" onClick={() => setShowModel(v => !v)}>{showModel ? "Hide" : "Show"} model traces ({modelCount})</button> : undefined}>
    {visible.length === 0 ? <Empty icon="clock" title="No events yet" /> : <ol className="timeline">{[...visible].reverse().map((event, index) => {
      const [, icon, tone] = ACTOR_ICON.find(([pattern]) => pattern.test(event.actor)) || [null, "user", "ok" as Tone];
      const hasDetails = event.details && Object.keys(event.details).length > 0;
      return <li key={event.id || index}>
        <span className={`timeline-icon tone-${tone}`}><Icon name={icon} size={14} /></span>
        <details><summary><b>{nice(event.event_type)}</b><small>{event.actor} · {clock(event.occurred_at)}</small></summary>
          {hasDetails && <pre className="raw">{JSON.stringify(event.details, null, 2)}</pre>}</details>
      </li>;
    })}</ol>}
  </Card>;
}
