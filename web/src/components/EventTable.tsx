"use client";

import { useState } from "react";
import type { Anomaly, RunRecord, Ticket } from "@/api/types";
import { call, messageOf } from "@/lib/client";
import { kw, kwh, utcDateTime } from "@/lib/format";
import Link from "next/link";
import { RunResult } from "./RunView";
import { Empty, Notice, Pill } from "./ui";

export function EventTable({
  site,
  events,
  canInvestigate,
  canDraft,
}: {
  site: string;
  events: Anomaly[];
  canInvestigate: boolean;
  canDraft: boolean;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, RunRecord>>({});
  const [tickets, setTickets] = useState<Record<string, Ticket>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  if (events.length === 0) {
    return <Empty title="Nothing unusual found">The detector compares each hour with the same hour on similar days.</Empty>;
  }

  async function investigate(id: string) {
    setBusy(`i-${id}`);
    setOpen(id);
    setErrors((e) => ({ ...e, [id]: "" }));
    try {
      const run = await call<RunRecord>("POST", "/agents/anomaly-investigator/runs", { body: { site, anomaly_id: id } });
      setRuns((r) => ({ ...r, [id]: run }));
    } catch (e) {
      setErrors((x) => ({ ...x, [id]: messageOf(e) }));
    } finally {
      setBusy(null);
    }
  }

  async function draft(id: string) {
    setBusy(`t-${id}`);
    setErrors((e) => ({ ...e, [id]: "" }));
    try {
      const ticket = await call<Ticket>("POST", "/tickets", { body: { site, anomaly_id: id } });
      setTickets((t) => ({ ...t, [id]: ticket }));
    } catch (e) {
      setErrors((x) => ({ ...x, [id]: messageOf(e) }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="sr-only">Unusual events at this site</caption>
        <thead>
          <tr>
            <th scope="col">Event</th>
            <th scope="col">When (UTC)</th>
            <th scope="col" className="num">Peak vs usual</th>
            <th scope="col" className="num">Energy difference</th>
            <th scope="col">Next step</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className="align-top">
              <td>
                <span className="font-semibold">{e.id}</span>{" "}
                <Pill tone={e.kind === "spike" ? "warn" : "neutral"}>{e.kind}</Pill>
              </td>
              <td>
                {utcDateTime(e.start)}
                <br />
                <span className="text-sm text-muted">to {utcDateTime(e.end)}</span>
              </td>
              <td className="num">
                {kw(e.observed_kw_at_peak)} vs {kw(e.baseline_kw_at_peak)}
              </td>
              <td className="num">
                {e.energy_difference_kwh > 0 ? "+" : ""}
                {kwh(e.energy_difference_kwh)}
              </td>
              <td>
                <div className="flex flex-wrap gap-2">
                  {canInvestigate ? (
                    <button className="btn btn-small" type="button" disabled={busy !== null} onClick={() => investigate(e.id)}>
                      {busy === `i-${e.id}` ? "Working" : "Investigate"}
                    </button>
                  ) : null}
                  {canDraft ? (
                    <button className="btn btn-small" type="button" disabled={busy !== null || Boolean(tickets[e.id])} onClick={() => draft(e.id)}>
                      {busy === `t-${e.id}` ? "Drafting" : "Draft ticket"}
                    </button>
                  ) : null}
                </div>
                <div aria-live="polite">
                  {errors[e.id] ? <p className="mt-2 text-sm text-brick">{errors[e.id]}</p> : null}
                  {tickets[e.id] ? (
                    <p className="mt-2 text-sm">
                      Ticket drafted. It waits for a manager to approve it on the{" "}
                      <Link className="underline" href="/tickets">
                        tickets page
                      </Link>
                      .
                    </p>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {open && runs[open] ? (
        <div className="mt-6 border border-line bg-surface p-4">
          <h3 className="mb-2 font-semibold">Possible causes for {open}</h3>
          <RunResult run={runs[open]} />
        </div>
      ) : open && busy === `i-${open}` ? (
        <div className="mt-6">
          <Notice>Investigating {open}. This can take a few seconds.</Notice>
        </div>
      ) : null}
    </div>
  );
}
