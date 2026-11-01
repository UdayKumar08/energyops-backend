import Link from "next/link";
import type { RunSummary } from "@/api/types";
import { utcDateTime } from "@/lib/format";
import { agentName, shorten, took } from "@/lib/runs";
import { Empty, StatusPill } from "./ui";

export function RunTable({ runs, filtered }: { runs: RunSummary[]; filtered: boolean }) {
  if (runs.length === 0) {
    return filtered ? (
      <Empty title="No runs match these filters">
        Try another agent or status, or clear the filters to see every run.
      </Empty>
    ) : (
      <Empty title="No runs yet">
        When someone runs an agent or asks the assistant a question, the answer is kept here so you can open it again.
      </Empty>
    );
  }
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="sr-only">Agent runs, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Started (UTC)</th>
            <th scope="col">Agent</th>
            <th scope="col">Question</th>
            <th scope="col">Result</th>
            <th scope="col">Took</th>
            <th scope="col">Model</th>
            <th scope="col">
              <span className="sr-only">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id}>
              <td className="whitespace-nowrap">{utcDateTime(r.started_at)}</td>
              <td className="font-semibold">{agentName(r.agent)}</td>
              <td className="max-w-[44ch] break-words">{shorten(r.question)}</td>
              <td>
                <StatusPill status={r.status} />
                {r.status === "failed" && r.error_kind ? (
                  <span className="mt-1 block text-xs text-muted">{r.error_kind.replaceAll("_", " ")}</span>
                ) : null}
              </td>
              <td className="whitespace-nowrap">{took(r.started_at, r.finished_at)}</td>
              <td className="text-sm text-muted">{r.model_name}</td>
              <td>
                <Link className="btn btn-small" href={`/runs/${r.run_id}`}>
                  Open
                  <span className="sr-only"> run from {utcDateTime(r.started_at)}</span>
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
