import type { Incident } from "./types";

export const TERMINAL = new Set(["verified", "denied", "rejected", "failed", "escalated", "policy_denied"]);

export function nice(value?: string | null): string {
  return (value || "unknown").replaceAll(/[_.]/g, " ").toLowerCase().replace(/^\w/, c => c.toUpperCase());
}
export const shortId = (value: string) => value.slice(0, 8);

export function ago(value?: string | null): string {
  if (!value) return "never";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
export function clock(value?: string | null): string {
  return value ? new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
}
export function stamp(value?: string | null): string {
  return value ? new Date(value).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
}

export type Tone = "ok" | "warn" | "bad" | "info" | "violet" | "idle";

export const STATUS: Record<string, { label: string; tone: Tone }> = {
  open: { label: "Open", tone: "info" },
  analyzing: { label: "Analyzing", tone: "violet" },
  awaiting_review: { label: "Needs your review", tone: "warn" },
  ready_for_pr: { label: "Approved", tone: "info" },
  pr_open: { label: "PR open", tone: "info" },
  merged_waiting_verification: { label: "Verifying recovery", tone: "violet" },
  deployed_verifying: { label: "Deployed · observing", tone: "violet" },
  verified: { label: "Recovery verified", tone: "ok" },
  escalated: { label: "Escalated to human", tone: "warn" },
  policy_denied: { label: "Blocked by policy", tone: "bad" },
  rejected: { label: "Rejected", tone: "idle" },
  failed: { label: "Analysis failed", tone: "bad" },
};
export const statusOf = (status: string) => STATUS[status] || { label: nice(status), tone: "idle" as Tone };

export const STAGES = ["Detect", "Diagnose", "Propose", "Review", "Pull request", "Verify"] as const;

/** Which pipeline stage an incident is in (0-5), and whether it stopped there. */
export function stageOf(incident: Incident): { index: number; stopped: boolean; done: boolean } {
  switch (incident.status) {
    case "open": return { index: 0, stopped: false, done: false };
    case "analyzing": return { index: 1, stopped: false, done: false };
    case "failed": return { index: 1, stopped: true, done: false };
    case "escalated": return { index: incident.diagnosis ? 1 : 0, stopped: true, done: false };
    case "policy_denied": return { index: 2, stopped: true, done: false };
    case "awaiting_review": return { index: 3, stopped: false, done: false };
    case "rejected": return { index: incident.pull_request ? 4 : 3, stopped: true, done: false };
    case "ready_for_pr": case "pr_open": return { index: 4, stopped: false, done: false };
    case "merged_waiting_verification": case "deployed_verifying": return { index: 5, stopped: false, done: false };
    case "verified": return { index: 5, stopped: false, done: true };
    default: return { index: 0, stopped: false, done: false };
  }
}

export const SOURCE_LABEL: Record<string, string> = {
  manual: "Reported manually", prometheus_auto: "Auto-detected", alertmanager: "Alertmanager",
};
