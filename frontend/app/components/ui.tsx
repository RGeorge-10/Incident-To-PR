import type { ReactNode } from "react";
import type { Tone } from "../lib/format";

const PATHS: Record<string, ReactNode> = {
  overview: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
  incidents: <><path d="M12 3 2.5 19.5h19L12 3Z" /><path d="M12 10v4.5" /><circle cx="12" cy="17" r=".6" fill="currentColor" /></>,
  setup: <><path d="M4 6h10M18 6h2M4 12h3M11 12h9M4 18h12M20 18h0" /><circle cx="16" cy="6" r="2" /><circle cx="9" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></>,
  lab: <><path d="M9 3h6M10 3v6.5L4.6 18a2 2 0 0 0 1.7 3h11.4a2 2 0 0 0 1.7-3L14 9.5V3" /><path d="M7.5 15h9" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8" /><path d="M4 4v4h4" /><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16" /><path d="M20 20v-4h-4" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  external: <><path d="M14 4h6v6" /><path d="M20 4 11 13" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>,
  spark: <path d="M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4L12 3ZM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z" />,
  shield: <><path d="M12 3 4.5 6v5.5c0 4.6 3.1 8 7.5 9.5 4.4-1.500 7.5-4.900 7.500-9.500V6L12 3Z" /><path d="m9 12 2.200 2.200L15.500 10" /></>,
  lock: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></>,
  git: <><circle cx="6" cy="6" r="2.200" /><circle cx="6" cy="18" r="2.200" /><circle cx="18" cy="9" r="2.200" /><path d="M6 8.200v7.600M18 11.200c0 3.300-3 3.800-6 3.800H8.200" /></>,
  radar: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><path d="M12 12 18.500 5.500" /><circle cx="12" cy="12" r="1" fill="currentColor" /></>,
  user: <><circle cx="12" cy="8" r="3.500" /><path d="M5 20c.600-3.600 3.400-5.500 7-5.500s6.400 1.900 7 5.500" /></>,
  cpu: <><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M10 10h4v4h-4zM9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" /></>,
  eye: <><path d="M2.500 12S6 5.500 12 5.500 21.500 12 21.500 12 18 18.500 12 18.500 2.500 12 2.500 12Z" /><circle cx="12" cy="12" r="2.800" /></>,
  flag: <><path d="M5 21V4M5 4.500c5-2.500 8 2.500 14 0v9c-6 2.500-9-2.500-14 0" /></>,
  rocket: <><path d="M14 4c3 0 6 3 6 6-2 5-6 7-9 8l-5-5c1-3 3-7 8-9Z" /><circle cx="14.500" cy="9.500" r="1.500" /><path d="M6 15l-2.500 5.500L9 18" /></>,
  layers: <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 13 9 5 9-5" /></>,
  chevron: <path d="m9 6 6 6-6 6" />,
  bolt: <path d="M13 2 4.500 13.500H11L10 22l8.500-11.500H12L13 2Z" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5.500l3.500 2" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5.500" /><circle cx="12" cy="7.800" r=".600" fill="currentColor" /></>,
  logo: <><path d="M4 13.500 9 4l3.200 6" /><path d="M10 20h9l-4.800-9.500L10 20Z" /></>,
};

export function Icon({ name, size = 18 }: { name: keyof typeof PATHS | string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{PATHS[name] || PATHS.info}</svg>;
}

export function Badge({ tone = "idle", children, pulse = false }: { tone?: Tone; children: ReactNode; pulse?: boolean }) {
  return <span className={`badge tone-${tone}`}><i className={pulse ? "dot pulse" : "dot"} />{children}</span>;
}

export function Card({ title, eyebrow, action, children, className = "" }: {
  title?: ReactNode; eyebrow?: string; action?: ReactNode; children: ReactNode; className?: string;
}) {
  return <section className={`card ${className}`}>
    {(title || eyebrow || action) && <header className="card-head">
      <div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}{title && <h3>{title}</h3>}</div>{action}
    </header>}
    {children}
  </section>;
}

export function Ring({ value, tone = "ok", label }: { value: number; tone?: Tone; label?: string }) {
  const clamped = Math.max(0, Math.min(1, value));
  const circumference = 2 * Math.PI * 26;
  return <div className={`ring tone-${tone}`} role="img" aria-label={`${label || "Value"} ${Math.round(clamped * 100)} percent`}>
    <svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="26" className="ring-track" />
      <circle cx="32" cy="32" r="26" className="ring-value" strokeDasharray={`${clamped * circumference} ${circumference}`} /></svg>
    <b>{Math.round(clamped * 100)}<small>%</small></b>
  </div>;
}

export function Empty({ icon = "info", title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return <div className="empty"><span className="empty-icon"><Icon name={icon} size={22} /></span><b>{title}</b>{children && <p>{children}</p>}</div>;
}
