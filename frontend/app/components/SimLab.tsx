"use client";

import { useCallback, useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { put } from "../lib/api";
import type { Configuration, SimState } from "../lib/types";
import { Badge, Card, Empty, Icon } from "./ui";

const KIND_TITLE: Record<string, string> = { capacity: "Capacity (infrastructure)", code_bug: "Code bug", ambiguous: "Ambiguous" };
const KIND_EXPECT: Record<string, string> = { capacity: "Expected: INFRA_FIX → replica PR", code_bug: "Expected: CODE_FIX → source PR", ambiguous: "Expected: escalate to human" };

export function SimLab({ busy, run, configuration }: { busy: boolean; configuration: Configuration | null; run: (message: string, operation: () => Promise<unknown>) => Promise<void> }) {
  const [model, setModel] = useState("anthropic/claude-haiku-5-5");
  const [apiKey, setApiKey] = useState("");
  const realModel = configuration?.model_source === "session";
  const [state, setState] = useState<SimState | null>(null);
  const [service, setService] = useState("shippingservice");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setState(await api<SimState>("/sim/state")); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Simulator unavailable"); }
  }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 3000); return () => window.clearInterval(timer); }, [load]);

  const act = (message: string, operation: () => Promise<unknown>) => run(message, async () => { await operation(); await load(); });

  if (!state) return <Card title="Simulation lab"><Empty icon="lab" title="Simulator not reachable">{error || "Loading…"}</Empty></Card>;
  const faulty = state.services.filter(s => s.fault);

  return <div className="lab">
    <p className="callout warn span-2"><Icon name="lab" size={18} /><span><b>Everything on this page is synthetic.</b> Telemetry is generated, the “model” is a deterministic rule-based stub, and GitHub/Argo CD are in-memory fakes. Use it to exercise the workflow and the UI — it is not evidence that real faults get diagnosed or repaired.</span></p>

    <Card eyebrow="Step 1" title="Inject a fault">
      <label className="field">Target deployment<select value={service} onChange={e => setService(e.target.value)}>{state.services.map(s => <option key={s.name}>{s.name}</option>)}</select></label>
      <div className="fault-kinds">{Object.entries(state.fault_kinds).map(([kind, description]) => <button key={kind} className="fault-kind" disabled={busy}
        onClick={() => void act(`Injected a ${kind.replace("_", " ")} fault into ${service}. With automatic detection on, an incident opens after two polls.`, () => post("/sim/faults", { service, kind }))}>
        <b>{KIND_TITLE[kind] || kind}</b><small>{description}</small><em>{KIND_EXPECT[kind]}</em></button>)}</div>
    </Card>

    <Card eyebrow="World state" title="Simulated cluster" action={<Badge tone={faulty.length ? "bad" : "ok"} pulse>{faulty.length ? `${faulty.length} faulted` : "All healthy"}</Badge>}>
      <ul className="sim-services">{state.services.map(s => <li key={s.name} className={s.available < s.desired ? "down" : ""}>
        <i /><b>{s.name}</b><small>{s.available}/{s.desired}</small>{s.fault && <span>{s.fault.replace("_", " ")}</span>}
        {s.fault && <button className="btn quiet small" disabled={busy} onClick={() => void act(`Cleared the fault on ${s.name}.`, () => api(`/sim/faults/${s.name}`, { method: "DELETE" }))}>Clear</button>}
      </li>)}</ul>
      <div className="row"><Badge tone={state.argocd.sync.status === "Synced" ? "ok" : "warn"}>Argo CD · {state.argocd.sync.status} / {state.argocd.health.status}</Badge>
        <button className="btn ghost small" disabled={busy} onClick={() => { if (window.confirm("Reset the simulated cluster, repository and pull requests?")) void act("Simulation reset to a healthy baseline.", () => post("/sim/reset")); }}><Icon name="refresh" size={14} />Reset world</button></div>
    </Card>

    <Card eyebrow="Model" title={realModel ? "Using a real model" : "Using the rule-based stub"} className="span-2"
      action={<Badge tone={realModel ? "violet" : "idle"}>{configuration?.model || "—"}</Badge>}>
      <p className="muted">{realModel ? "Diagnosis, decision and patch now come from this model. Telemetry, GitHub and Argo CD are still simulated. Incident text, synthetic logs and synthetic source files are sent to the provider."
        : "To have a real LLM make the decisions, enter a LiteLLM model id and your API key. The key is sent to this lab's API over this page, checked with one small request, kept in memory only, and forgotten when the lab stops."}</p>
      <form className="form model-form" autoComplete="off" onSubmit={event => { event.preventDefault(); void run(`Now using ${model}.`, async () => { await put("/api/v1/configuration/model", { model, api_key: apiKey }); setApiKey(""); }); }}>
        <label>Model id<input value={model} onChange={e => setModel(e.target.value)} placeholder="anthropic/claude-haiku-5-5" required /></label>
        <label>API key<input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder="sk-ant-…" autoComplete="new-password" required /></label>
        <div className="row"><button className="btn primary" disabled={busy || !apiKey.trim()}>{realModel ? "Replace model" : "Use this model"}</button>
          {realModel && <button type="button" className="btn ghost" disabled={busy} onClick={() => void run("Back on the rule-based stub.", () => api("/api/v1/configuration/model", { method: "DELETE" }))}>Back to the stub</button>}</div>
      </form>
    </Card>

    <Card eyebrow="Step 2 · the human reviewer" title="Simulated GitHub pull requests" className="span-2">
      <p className="muted">The control plane can only open pull requests. Merging is done by a person on GitHub — here, on a stand-in page — after which Argo CD syncs and the verifier can observe recovery.</p>
      {state.pulls.length === 0 ? <Empty icon="git" title="No pull requests yet">Approve a proposal in an incident, then open its pull request.</Empty>
        : <ul className="sim-pulls">{state.pulls.map(pr => <li key={pr.number}><Icon name="git" size={16} /><div><b>#{pr.number} {pr.title}</b><small>{state.repository}</small></div>
          <Badge tone={pr.merged ? "violet" : pr.state === "open" ? "ok" : "idle"}>{pr.merged ? "Merged" : pr.state}</Badge>
          <a className="btn ghost small" href={pr.url} target="_blank" rel="noreferrer">Open<Icon name="external" size={14} /></a></li>)}</ul>}
    </Card>
  </div>;
}
