import type { ReactNode } from "react";

export function PageHeader({
  title,
  lead,
  actions,
}: {
  title: string;
  lead?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4 border-b-2 border-ink pb-4">
      <div className="max-w-[60ch]">
        <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
        {lead ? <p className="mt-1 text-muted">{lead}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </header>
  );
}

export function Section({
  title,
  note,
  children,
  actions,
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="mb-10">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xl font-semibold">{title}</h2>
        {actions}
      </div>
      {note ? <p className="mb-3 max-w-[70ch] text-sm text-muted">{note}</p> : null}
      {children}
    </section>
  );
}

type Tone = "ok" | "warn" | "bad" | "neutral";

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  const cls = tone === "ok" ? "pill-ok" : tone === "warn" ? "pill-warn" : tone === "bad" ? "pill-bad" : "";
  return <span className={`pill ${cls}`}>{children}</span>;
}

export function Notice({
  tone = "neutral",
  children,
}: {
  tone?: Tone;
  children: ReactNode;
}) {
  const cls = tone === "warn" ? "notice-warn" : tone === "bad" ? "notice-bad" : "";
  return (
    <div className={`notice ${cls}`} role={tone === "bad" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="border border-dashed border-line px-6 py-8 text-center">
      <p className="font-semibold">{title}</p>
      {children ? <div className="mx-auto mt-1 max-w-[50ch] text-sm text-muted">{children}</div> : null}
    </div>
  );
}

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: ReactNode;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div className="mb-4">
      <label className="label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint ? <p className="hint">{hint}</p> : null}
    </div>
  );
}

export function statusTone(status: string): Tone {
  if (["active", "approved", "succeeded", "answered"].includes(status)) return "ok";
  if (["draft", "pending_approval", "needs_input", "ticket_pending"].includes(status)) return "warn";
  if (["disabled", "rejected", "failed", "declined"].includes(status)) return "bad";
  return "neutral";
}

export function StatusPill({ status }: { status: string }) {
  return <Pill tone={statusTone(status)}>{status.replaceAll("_", " ")}</Pill>;
}
