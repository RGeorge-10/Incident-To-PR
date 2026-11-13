"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { IncidentDetail } from "./components/IncidentDetail";
import { Overview } from "./components/Overview";
import { Setup } from "./components/Setup";
import { SimLab } from "./components/SimLab";
import { Badge, Empty, Icon } from "./components/ui";
import { api, post } from "./lib/api";
import { ago, shortId, SOURCE_LABEL, statusOf, TERMINAL } from "./lib/format";
import type { Configuration, Fleet, Incident, Integrations, Policy } from "./lib/types";

type View = "overview" | "incidents" | "setup" | "lab";
type Filter = "all" | "active" | "review" | "closed";

const VIEW_TITLE: Record<View, [string, string]> = {
  overview: ["Command center", "Live health, the remediation pipeline and everything waiting on you."],
  incidents: ["Incidents", "Evidence, diagnosis, policy checks and the change proposed for each incident."],
  setup: ["Connections & policy", "Repositories, automatic detection, integrations and the safety policy."],
  lab: ["Simulation lab", "Inject synthetic faults and play the human reviewer to exercise the whole workflow."],
};

export default function Home() {
  const [view, setView] = useState<View>("overview");
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [detail, setDetail] = useState<Incident | null>(null);
  const [selected, setSelected] = useState("");
  const [integrations, setIntegrations] = useState<Integrations>({});
  const [configuration, setConfiguration] = useState<Configuration | null>(null);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [reporting, setReporting] = useState(false);
  const [service, setService] = useState("");
  const [summary, setSummary] = useState("");
  const selectedRef = useRef("");
  selectedRef.current = selected;

  const loadDetail = useCallback(async (id: string) => {
    if (!id) { setDetail(null); return; }
    try {
      const incident = await api<Incident>(`/api/v1/incidents/${id}`);
      if (selectedRef.current === id) setDetail(incident);
    } catch { /* the list refresh reports connectivity problems */ }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [rows, checks, config, fleetState] = await Promise.all([
        api<Incident[]>("/api/v1/incidents"), api<Integrations>("/api/v1/integrations"),
        api<Configuration>("/api/v1/configuration"), api<Fleet>("/api/v1/fleet")]);
      setIncidents(rows); setIntegrations(checks); setConfiguration(config); setFleet(fleetState); setOnline(true);
      const current = selectedRef.current && rows.some(r => r.id === selectedRef.current) ? selectedRef.current : rows[0]?.id || "";
      if (current !== selectedRef.current) { selectedRef.current = current; setSelected(current); }
      await loadDetail(current);
    } catch { setOnline(false); }
  }, [loadDetail]);

  useEffect(() => { api<Policy>("/api/v1/policy").then(setPolicy).catch(() => undefined); }, [online]);

  const analyzing = incidents.some(i => i.status === "analyzing");
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), analyzing ? 1500 : 5000);
    return () => window.clearInterval(timer);
  }, [refresh, analyzing]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), toast.tone === "bad" ? 9000 : 5000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const run = useCallback(async (message: string, operation: () => Promise<unknown>) => {
    setBusy(true); setToast(null);
    try { await operation(); await refresh(); setToast({ tone: "ok", text: message }); }
    catch (e) { setToast({ tone: "bad", text: e instanceof Error ? e.message : "Action failed" }); }
    finally { setBusy(false); }
  }, [refresh]);

  const openIncident = useCallback((id: string) => {
    selectedRef.current = id; setSelected(id); setDetail(null); setView("incidents"); void loadDetail(id);
  }, [loadDetail]);

  const createIncident = async (event: FormEvent) => {
    event.preventDefault();
    await run("Incident created. Run the analysis when you are ready.", async () => {
      const created = await post<Incident>("/api/v1/incidents", { service: service.trim(), summary: summary.trim(), source: "manual" });
      selectedRef.current = created.id; setSelected(created.id); setDetail(null);
      setSummary(""); setReporting(false); setView("incidents");
    });
  };

  const filtered = useMemo(() => incidents.filter(i => filter === "all" ? true : filter === "active" ? !TERMINAL.has(i.status)
    : filter === "review" ? i.status === "awaiting_review" : TERMINAL.has(i.status)), [incidents, filter]);
  const active = detail && detail.id === selected ? detail : incidents.find(i => i.id === selected) || null;
  const reviewCount = incidents.filter(i => i.status === "awaiting_review").length;
  const simulation = Boolean(configuration?.simulation);
  const services = fleet?.deployments.map(d => d.name) || [];
  const nav: Array<{ id: View; label: string; icon: string; count?: number }> = [
    { id: "overview", label: "Overview", icon: "overview" },
    { id: "incidents", label: "Incidents", icon: "incidents", count: reviewCount || undefined },
    { id: "setup", label: "Connections & policy", icon: "setup" },
    ...(simulation ? [{ id: "lab" as View, label: "Simulation lab", icon: "lab" }] : []),
  ];

  return <div className="app">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><Icon name="logo" size={20} /></span><div><b>Incident-to-PR</b><small>Remediation control room</small></div></div>
      <nav aria-label="Main navigation">{nav.map(item => <button key={item.id} className={view === item.id ? "nav-item active" : "nav-item"} onClick={() => setView(item.id)} aria-current={view === item.id ? "page" : undefined}>
        <Icon name={item.icon} /><span>{item.label}</span>{item.count ? <em>{item.count}</em> : null}</button>)}</nav>
      <div className="sidebar-foot">
        <div className="env"><i className={`led tone-${online ? "ok" : online === false ? "bad" : "idle"}`} /><div><b>{online ? "Control plane online" : online === false ? "Control plane offline" : "Connecting…"}</b>
          <small>{simulation ? "Simulation lab" : configuration ? `${configuration.environment} · ${configuration.namespace}` : "—"}</small></div></div>
        <p>Agents propose. Policy decides. Humans approve. GitOps applies.</p>
      </div>
    </aside>

    <div className="main">
      {simulation && <div className="sim-banner"><Icon name="lab" size={16} /><span><b>Simulation lab.</b> Synthetic telemetry, {configuration?.model_source === "session" ? <>decisions by <b>{configuration.model}</b>,</> : "a rule-based stub model"} and an in-memory GitHub/Argo CD. Nothing here is a real repair result.</span></div>}
      {online === false && <div className="sim-banner bad"><Icon name="info" size={16} /><span><b>The control-plane API is not reachable.</b> Start it, and this page reconnects automatically.</span></div>}

      <header className="topbar">
        <div><h1>{VIEW_TITLE[view][0]}</h1><p>{VIEW_TITLE[view][1]}</p></div>
        <div className="topbar-actions">
          <Badge tone={configuration?.monitoring.enabled ? "ok" : "idle"} pulse={Boolean(configuration?.monitoring.enabled)}>{configuration?.monitoring.enabled ? "Auto-detection on" : "Manual mode"}</Badge>
          <button className="btn ghost icon" onClick={() => void refresh()} title="Refresh now" aria-label="Refresh now"><Icon name="refresh" size={16} /></button>
          <button className="btn primary" onClick={() => { setService(s => s || services[0] || ""); setReporting(true); }}><Icon name="plus" size={16} />Report incident</button>
        </div>
      </header>

      {view === "overview" && <Overview incidents={incidents} fleet={fleet} configuration={configuration} integrations={integrations}
        onOpenIncident={openIncident} onNavigate={setView} onReport={() => { setService(s => s || services[0] || ""); setReporting(true); }} />}

      {view === "incidents" && <div className="workspace">
        <aside className="queue card">
          <header className="card-head"><div><p className="eyebrow">Work queue</p><h3>Incidents</h3></div><span className="count">{filtered.length}</span></header>
          <div className="filters" role="tablist">{(["all", "active", "review", "closed"] as Filter[]).map(f => <button key={f} role="tab" aria-selected={filter === f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>{f === "review" ? "Needs review" : f[0].toUpperCase() + f.slice(1)}</button>)}</div>
          {filtered.length === 0 ? <Empty icon="incidents" title="Nothing here">{incidents.length ? "No incidents match this filter." : "Report an incident or enable automatic detection."}</Empty>
            : <div className="queue-list">{filtered.map(item => {
              const status = statusOf(item.status);
              return <button key={item.id} className={selected === item.id ? "queue-item selected" : "queue-item"} onClick={() => openIncident(item.id)}>
                <div className="queue-top"><Badge tone={status.tone} pulse={item.status === "analyzing"}>{status.label}</Badge><time>{ago(item.created_at)}</time></div>
                <b>{item.service}</b><p>{item.summary}</p>
                <small>#{shortId(item.id)} · {SOURCE_LABEL[item.source] || item.source}</small>
              </button>;
            })}</div>}
        </aside>
        {active ? <IncidentDetail key={active.id} incident={active} configuration={configuration} busy={busy} run={run} />
          : <section className="card"><Empty icon="incidents" title="Select an incident">Evidence, diagnosis, policy checks, the proposed diff and recovery status appear here.</Empty></section>}
      </div>}

      {view === "setup" && <Setup configuration={configuration} integrations={integrations} policy={policy} busy={busy} run={run} />}
      {view === "lab" && simulation && <SimLab busy={busy} run={run} configuration={configuration} />}

      <footer className="footer"><span>Incident-to-PR · Phase I research prototype</span><span>The application never merges a pull request and never writes to Kubernetes.</span></footer>
    </div>

    {reporting && <div className="modal-backdrop" onClick={() => setReporting(false)}>
      <form className="modal card" onSubmit={createIncident} onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Report an incident">
        <header className="card-head"><div><p className="eyebrow">Manual report</p><h3>Report an incident</h3></div>
          <button type="button" className="btn quiet icon" onClick={() => setReporting(false)} aria-label="Close"><Icon name="x" size={16} /></button></header>
        <label>Service / deployment name
          <input value={service} onChange={e => setService(e.target.value)} list="services" placeholder="shippingservice" maxLength={200} required autoFocus />
          <datalist id="services">{services.map(name => <option key={name} value={name} />)}</datalist></label>
        <label>What are you seeing?
          <textarea value={summary} onChange={e => setSummary(e.target.value)} rows={4} maxLength={2000} required placeholder="Example: checkout requests time out since the last deployment; shipping quotes return 500." /></label>
        <p className="fine">The report is a symptom, not proof. A diagnosis must also cite collected telemetry, or the incident is escalated.</p>
        <div className="modal-actions"><button type="button" className="btn ghost" onClick={() => setReporting(false)}>Cancel</button>
          <button className="btn primary" disabled={busy || !service.trim() || !summary.trim()}>Create incident<Icon name="arrow" size={16} /></button></div>
      </form>
    </div>}

    {toast && <div className={`toast tone-${toast.tone}`} role="status"><Icon name={toast.tone === "ok" ? "check" : "info"} size={16} /><span>{toast.text}</span>
      <button onClick={() => setToast(null)} aria-label="Dismiss"><Icon name="x" size={14} /></button></div>}
  </div>;
}
