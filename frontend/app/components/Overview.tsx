"use client";

import { ago, SOURCE_LABEL, STAGES, stageOf, statusOf, TERMINAL } from "../lib/format";
import type { Configuration, Fleet, Incident, Integrations } from "../lib/types";
import { Badge, Card, Empty, Icon } from "./ui";

type Props = {
  incidents: Incident[]; fleet: Fleet | null; configuration: Configuration | null; integrations: Integrations;
  onOpenIncident: (id: string) => void; onNavigate: (view: "incidents" | "setup" | "lab") => void; onReport: () => void;
};

const STAGE_ICON = ["eye", "cpu", "layers", "user", "git", "shield"];
const STAGE_HINT = ["Health signals & reports", "Evidence-backed diagnosis", "Policy-gated patch", "Human approval", "Branch + PR, never merge", "Argo CD + sustained health"];

export function Overview({ incidents, fleet, configuration, integrations, onOpenIncident, onNavigate, onReport }: Props) {
  const active = incidents.filter(i => !TERMINAL.has(i.status));
  const review = incidents.filter(i => i.status === "awaiting_review");
  const prs = incidents.filter(i => ["ready_for_pr", "pr_open", "merged_waiting_verification", "deployed_verifying"].includes(i.status));
  const verified = incidents.filter(i => i.status === "verified");
  const stageCounts = STAGES.map((_, index) => active.filter(i => stageOf(i).index === index).length);
  const deployments = fleet?.deployments || [];
  const degraded = deployments.filter(d => !d.healthy);
  const monitoring = configuration?.monitoring;
  const fleetKnown = Boolean(fleet?.configured && !fleet.error && deployments.length);

  const headline = !fleetKnown ? "Telemetry is not connected yet"
    : degraded.length ? `${degraded.length} of ${deployments.length} services need attention`
    : `All ${deployments.length} services are healthy`;
  const tone = !fleetKnown ? "idle" : degraded.length ? "bad" : "ok";

  return <div className="overview">
    <section className={`hero tone-${tone}`}>
      <div className="hero-orb" aria-hidden="true"><span /><span /><span /><i /></div>
      <div className="hero-copy">
        <p className="eyebrow">Namespace · {configuration?.namespace || "—"}</p>
        <h2>{headline}</h2>
        <p>{!fleetKnown ? "Connect Prometheus to see live replica health, or start the simulation lab to explore the full workflow."
          : degraded.length ? `Degraded: ${degraded.map(d => d.name).join(", ")}. ${monitoring?.enabled ? "Automatic detection is watching." : "Automatic detection is off — report the incident or enable monitoring."}`
          : active.length ? `${active.length} incident${active.length === 1 ? " is" : "s are"} still moving through the pipeline.` : "No open incidents. The pipeline is idle."}</p>
        <div className="hero-actions">
          <button className="btn primary" onClick={onReport}><Icon name="plus" size={16} />Report an incident</button>
          {review.length > 0 && <button className="btn ghost" onClick={() => onOpenIncident(review[0].id)}>Review {review.length} proposal{review.length === 1 ? "" : "s"}<Icon name="arrow" size={16} /></button>}
        </div>
      </div>
      <ul className="invariants">
        <li><Icon name="lock" size={16} /><span><b>No cluster writes</b>Read-only observer token</span></li>
        <li><Icon name="user" size={16} /><span><b>Human approval</b>Required before every PR</span></li>
        <li><Icon name="git" size={16} /><span><b>PR only</b>The bot can never merge</span></li>
      </ul>
    </section>

    <section className="kpis">
      <Kpi icon="incidents" tone="info" value={active.length} label="Active incidents" hint={`${incidents.length} total`} />
      <Kpi icon="user" tone="warn" value={review.length} label="Awaiting your review" hint={review.length ? "Approve or reject" : "Nothing waiting"} />
      <Kpi icon="git" tone="violet" value={prs.length} label="Pull requests in flight" hint="Merge stays on GitHub" />
      <Kpi icon="shield" tone="ok" value={verified.length} label="Verified recoveries" hint={`${configuration?.verification_window_minutes ?? 15}-minute health window`} />
    </section>

    <Card eyebrow="Remediation pipeline" title="Where every active incident is right now" className="pipeline-card">
      <ol className="pipeline">
        {STAGES.map((stage, index) => <li key={stage} className={stageCounts[index] ? "has" : ""}>
          <span className="pipeline-icon"><Icon name={STAGE_ICON[index]} size={20} /></span>
          <b>{stage}</b><small>{STAGE_HINT[index]}</small>
          <em>{stageCounts[index]}</em>
        </li>)}
      </ol>
    </Card>

    <div className="overview-grid">
      <Card eyebrow="Fleet health" title="Deployments" className="fleet-card"
        action={fleetKnown ? <Badge tone={degraded.length ? "bad" : "ok"} pulse>{degraded.length ? `${degraded.length} degraded` : "Healthy"}</Badge> : undefined}>
        {!fleetKnown ? <Empty icon="radar" title="No replica data">{fleet?.error || "Prometheus with kube-state-metrics is not reachable."}</Empty>
          : <div className="fleet">{deployments.map(d => {
            const ratio = d.desired ? Math.min(1, d.available / d.desired) : 1;
            return <div key={d.name} className={d.healthy ? "svc" : "svc down"}>
              <div className="svc-top"><i /><b>{d.name}</b></div>
              <div className="svc-bar"><span style={{ width: `${ratio * 100}%` }} /></div>
              <small>{d.available}/{d.desired} replicas{d.healthy ? "" : " · unavailable"}</small>
            </div>;
          })}</div>}
      </Card>

      <div className="stack">
        <Card eyebrow="Automatic detection" title={monitoring?.enabled ? "Watching the runtime" : "Monitoring is off"}
          action={<Badge tone={monitoring?.enabled ? (monitoring.last_poll.ok === false ? "bad" : "ok") : "idle"} pulse={Boolean(monitoring?.enabled)}>{monitoring?.enabled ? "Live" : "Stopped"}</Badge>}>
          <div className={monitoring?.enabled ? "radar on" : "radar"} aria-hidden="true"><span /><span /><span /><i /></div>
          {monitoring?.enabled ? <dl className="facts">
            <div><dt>Last poll</dt><dd>{monitoring.last_poll.at ? ago(monitoring.last_poll.at) : "starting…"}</dd></div>
            <div><dt>Interval</dt><dd>every {monitoring.poll_seconds}s</dd></div>
            <div><dt>Debounce</dt><dd>{monitoring.consecutive_polls} unhealthy polls</dd></div>
            <div><dt>Analysis</dt><dd>{monitoring.auto_analyze ? "automatic" : "manual"}</dd></div>
          </dl> : <p className="muted">Turn on monitoring to open a deduplicated incident when replicas are unavailable, a pod crash-loops, or containers restart repeatedly.</p>}
          {monitoring?.enabled && monitoring.last_poll.ok === false && <p className="inline-warn">Last poll failed: {monitoring.last_poll.error}</p>}
          <button className="btn ghost small" onClick={() => onNavigate("setup")}>{monitoring?.enabled ? "Change settings" : "Enable in Connections"}<Icon name="arrow" size={14} /></button>
        </Card>

        <Card eyebrow="Integrations" title="Connections">
          <ul className="mini-integrations">{Object.entries(integrations).filter(([name]) => name !== "evaluator").map(([name, item]) => {
            const state = item.configured && item.reachable !== false ? "ok" : item.configured ? "bad" : "idle";
            return <li key={name}><i className={`led tone-${state}`} /><span>{name === "argocd" ? "Argo CD" : name === "github" ? "GitHub" : name.replace(/^\w/, c => c.toUpperCase())}</span>
              <small>{state === "ok" ? "Connected" : state === "bad" ? "Unreachable" : "Not set up"}</small></li>;
          })}</ul>
        </Card>
      </div>
    </div>

    <Card eyebrow="Activity" title="Latest incidents" action={<button className="btn ghost small" onClick={() => onNavigate("incidents")}>View all<Icon name="arrow" size={14} /></button>}>
      {incidents.length === 0 ? <Empty icon="incidents" title="No incidents yet">Report one manually, enable automatic detection, or inject a fault from the simulation lab.</Empty>
        : <div className="activity">{incidents.slice(0, 6).map(incident => {
          const status = statusOf(incident.status);
          return <button key={incident.id} className="activity-row" onClick={() => onOpenIncident(incident.id)}>
            <span className={`activity-mark tone-${status.tone}`}><Icon name={incident.source === "manual" ? "user" : "radar"} size={16} /></span>
            <span className="activity-main"><b>{incident.service}</b><small>{incident.summary}</small></span>
            <span className="activity-meta"><small>{SOURCE_LABEL[incident.source] || incident.source}</small><small>{ago(incident.created_at)}</small></span>
            <Badge tone={status.tone} pulse={incident.status === "analyzing"}>{status.label}</Badge>
            <Icon name="chevron" size={16} />
          </button>;
        })}</div>}
    </Card>
  </div>;
}

function Kpi({ icon, tone, value, label, hint }: { icon: string; tone: string; value: number; label: string; hint: string }) {
  return <div className={`kpi tone-${tone}`}>
    <span className="kpi-icon"><Icon name={icon} size={20} /></span>
    <div><b>{value}</b><span>{label}</span><small>{hint}</small></div>
  </div>;
}
