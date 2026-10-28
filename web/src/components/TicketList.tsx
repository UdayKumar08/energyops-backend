"use client";

import { useState } from "react";
import type { Ticket } from "@/api/types";
import { call, messageOf } from "@/lib/client";
import { utcDateTime } from "@/lib/format";
import { Empty, Notice, Pill, StatusPill } from "./ui";

function TicketCard({ initial, canApprove }: { initial: Ticket; canApprove: boolean }) {
  const [ticket, setTicket] = useState(initial);
  const [note, setNote] = useState("");
  const [priority, setPriority] = useState(initial.priority);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      setTicket(
        await call<Ticket>("POST", `/tickets/${ticket.id}/decision`, {
          body: { approve, note: note.trim() || null, priority: approve ? priority : null },
        }),
      );
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="max-w-[60ch] font-semibold">{ticket.title}</h3>
        <div className="flex gap-2">
          <Pill tone={ticket.priority === "high" ? "bad" : ticket.priority === "medium" ? "warn" : "neutral"}>
            {ticket.priority} priority
          </Pill>
          <StatusPill status={ticket.status} />
        </div>
      </div>
      <p className="mt-1 text-sm text-muted">
        {ticket.site_name}, event {ticket.anomaly_id ?? "none"}, drafted {utcDateTime(ticket.created_at)}
      </p>
      <p className="mt-3 max-w-[70ch] whitespace-pre-wrap">{ticket.description}</p>
      {ticket.evidence.length > 0 ? (
        <ul className="mt-3 list-disc pl-5 text-sm">
          {ticket.evidence.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <p className="mt-3 text-sm text-muted">{ticket.note}</p>

      {ticket.decided_at ? (
        <p className="mt-3 text-sm">
          Decided {utcDateTime(ticket.decided_at)}
          {ticket.decision_note ? `. Note: ${ticket.decision_note}` : "."}
        </p>
      ) : canApprove && ticket.awaiting_approval ? (
        <div className="mt-4 border-t border-line pt-4">
          <div className="grid gap-4 sm:grid-cols-[1fr_180px]">
            <div>
              <label className="label" htmlFor={`note-${ticket.id}`}>
                Note (optional)
              </label>
              <input id={`note-${ticket.id}`} className="field" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor={`pri-${ticket.id}`}>
                Priority if approved
              </label>
              <select id={`pri-${ticket.id}`} className="field" value={priority} onChange={(e) => setPriority(e.target.value)}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button className="btn btn-primary" type="button" disabled={busy} onClick={() => decide(true)}>
              Approve ticket
            </button>
            <button className="btn btn-danger" type="button" disabled={busy} onClick={() => decide(false)}>
              Reject ticket
            </button>
          </div>
        </div>
      ) : ticket.awaiting_approval ? (
        <p className="mt-3 text-sm text-muted">Waiting for a manager to decide.</p>
      ) : null}
      {error ? (
        <div className="mt-3">
          <Notice tone="bad">{error}</Notice>
        </div>
      ) : null}
    </article>
  );
}

export function TicketList({ tickets, canApprove }: { tickets: Ticket[]; canApprove: boolean }) {
  if (tickets.length === 0) {
    return (
      <Empty title="No tickets yet">
        Draft one from an unusual event on the overview page, or ask the assistant to raise one.
      </Empty>
    );
  }
  return (
    <div className="space-y-4">
      {tickets.map((t) => (
        <TicketCard key={t.id} initial={t} canApprove={canApprove} />
      ))}
    </div>
  );
}
