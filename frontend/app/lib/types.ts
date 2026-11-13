export type Json = Record<string, unknown>;

export type Patch = { path: string; content: string; base_content?: string; sha?: string };
export type PolicyCheck = { name: string; passed: boolean; detail?: string };
export type AuditEvent = { id?: string; occurred_at: string; actor: string; event_type: string; details: Json };

export type Evidence = Json & {
  id?: string; source?: string; configured?: boolean; error?: string; collected_at?: string; query?: string;
  items?: unknown[]; summary?: string;
};

export type Diagnosis = {
  root_cause?: string; fault_class?: string; confidence?: number; evidence_ids?: string[];
  contradictions?: string[]; missing_evidence?: string[]; rationale?: string; guard?: string;
};

export type Incident = {
  id: string; service: string; summary: string; source: string; status: string;
  created_at: string; updated_at: string; analyzed_at?: string; labels?: Json;
  evidence: Evidence[]; diagnosis?: Diagnosis | null;
  decision?: { action?: string; confidence?: number; rationale?: string; overridden_by_guard?: string[] } | null;
  proposal?: { summary?: string; rollback_plan?: string; patches?: Patch[]; repository?: string; base_branch?: string } | null;
  policy?: { allowed?: boolean; requires_approval?: boolean; reasons?: string[]; rule_version?: string; checks?: PolicyCheck[] } | null;
  review?: { decision?: string; reviewer?: string; comment?: string; at?: string } | null;
  pull_request?: { number: number; url: string; state: string; merged?: boolean; merged_at?: string; repository: string; head_branch?: string; base_branch?: string } | null;
  verification?: Verification | null; failure?: string | null; events?: AuditEvent[];
};

export type HealthWindow = {
  configured?: boolean; stable?: boolean; window_minutes?: number; elapsed_seconds?: number;
  samples?: number; healthy_samples?: number; minimum_samples?: number; reason?: string | null;
};
export type Verification = {
  argocd?: { configured?: boolean; synced?: boolean; healthy?: boolean; sync_status?: string; health_status?: string; revision?: string; reason?: string };
  prometheus?: { configured?: boolean; healthy?: boolean; values?: number[]; reason?: string };
  prometheus_windows?: Record<string, HealthWindow>; checked_at?: string; started_at?: string;
};

export type Integration = {
  configured?: boolean; reachable?: boolean; detail?: string | null; mode?: string; reason?: string | null;
  sync_status?: string; health_status?: string;
};
export type Integrations = Record<string, Integration>;

export type Configuration = {
  code_repository: string; gitops_repository: string; github_token_configured: boolean; model_configured: boolean;
  model?: string | null; model_source?: string; namespace: string; base_branch: string; simulation: boolean; environment: string;
  verification_window_minutes: number; evidence_max_age_seconds: number;
  monitoring: {
    enabled: boolean; auto_analyze: boolean; poll_seconds: number; consecutive_polls: number;
    last_poll: { at: string | null; ok: boolean | null; error: string | null; watched: number; unhealthy: Array<{ service: string; signals: string[] }> };
  };
};

export type Policy = {
  require_human_approval: boolean; auto_apply_enabled: boolean; code_allowed_paths: string[]; infra_allowed_paths: string[];
  code_context_files: string[]; infra_context_files: string[]; max_patch_files: number; max_patch_bytes: number;
  infra_change_scope: string; evidence_max_age_seconds: number; rule_version: string; invariant: string;
};

export type Deployment = { name: string; desired: number; available: number; healthy: boolean };
export type Fleet = { configured: boolean; deployments: Deployment[]; error?: string; checked_at?: string };

export type SimState = {
  repository: string; fault_kinds: Record<string, string>;
  services: Array<{ name: string; desired: number; available: number; fault: string | null }>;
  pulls: Array<{ number: number; title: string; state: string; merged: boolean; url: string }>;
  argocd: { sync: { status: string }; health: { status: string } };
};
