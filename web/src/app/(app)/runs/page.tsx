import Link from "next/link";
import type { RunSummary } from "@/api/types";
import { RunTable } from "@/components/RunTable";
import { Notice, PageHeader } from "@/components/ui";
import { backend } from "@/lib/backend";
import { settle } from "@/lib/safe";
import { agentName, agentsIn, filterRuns, readFilter } from "@/lib/runs";

export const metadata = { title: "Run history" };

const LIMIT = 200;

export default async function RunsPage({
  searchParams,
}: {
  searchParams: Promise<{ agent?: string | string[]; status?: string | string[] }>;
}) {
  const filter = readFilter(await searchParams);
  const result = await settle(backend<RunSummary[]>(`/runs?limit=${LIMIT}`));
  const all = result.data ?? [];
  const shown = filterRuns(all, filter);
  const filtered = filter.agent !== "" || filter.status !== "";

  return (
    <>
      <PageHeader
        title="Run history"
        lead="Every answer an agent has given for you, newest first. Open one to read it again with the checks it passed."
      />
      {result.error ? (
        <Notice tone="bad">{result.error}</Notice>
      ) : (
        <>
          <form method="get" className="mb-6 flex flex-wrap items-end gap-4" aria-label="Filter runs">
            <div className="min-w-[14rem]">
              <label className="label" htmlFor="run-agent">
                Agent
              </label>
              <select id="run-agent" name="agent" className="field" defaultValue={filter.agent}>
                <option value="">All agents</option>
                {agentsIn(all).map((id) => (
                  <option key={id} value={id}>
                    {agentName(id)}
                  </option>
                ))}
              </select>
            </div>
            <div className="min-w-[12rem]">
              <label className="label" htmlFor="run-status">
                Result
              </label>
              <select id="run-status" name="status" className="field" defaultValue={filter.status}>
                <option value="">Any result</option>
                <option value="succeeded">Succeeded</option>
                <option value="failed">Failed</option>
              </select>
            </div>
            <button type="submit" className="btn btn-primary">
              Apply filters
            </button>
            {filtered ? (
              <Link className="btn" href="/runs">
                Clear filters
              </Link>
            ) : null}
          </form>
          <p className="mb-3 text-sm text-muted" aria-live="polite">
            Showing {shown.length} of {all.length} {all.length === 1 ? "run" : "runs"}
            {all.length >= LIMIT ? ` (the latest ${LIMIT})` : ""}.
          </p>
          <RunTable runs={shown} filtered={filtered} />
        </>
      )}
    </>
  );
}
