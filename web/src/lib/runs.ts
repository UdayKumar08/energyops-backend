import type { RunSummary } from "@/api/types";

export type RunFilter = { agent: string; status: "" | "succeeded" | "failed" };

/** Read the filter from the page address. Anything unexpected is treated as "no filter". */
export function readFilter(search: { agent?: string | string[]; status?: string | string[] }): RunFilter {
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";
  const status = one(search.status);
  return {
    agent: one(search.agent).slice(0, 64),
    status: status === "succeeded" || status === "failed" ? status : "",
  };
}

export function filterRuns(runs: readonly RunSummary[], filter: RunFilter): RunSummary[] {
  return runs.filter(
    (r) => (filter.agent === "" || r.agent === filter.agent) && (filter.status === "" || r.status === filter.status),
  );
}

/** The agents that appear in a list of runs, sorted, each once. */
export function agentsIn(runs: readonly RunSummary[]): string[] {
  return [...new Set(runs.map((r) => r.agent))].sort();
}

/** How long a run took, in words a person reads at a glance. */
export function took(startedAt: string, finishedAt: string): string {
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "n/a";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

/** A machine id like "savings-advisor" becomes "Savings advisor". A name that already reads as
 * words, like "EnergyOps Savings Advisor", is left as it is. */
export function agentName(id: string): string {
  if (/[\sA-Z]/.test(id)) return id;
  const words = id.replaceAll(/[-_]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : id;
}

/** A long question cut to a length a table row can hold. */
export function shorten(text: string, max = 110): string {
  const flat = text.replaceAll(/\s+/g, " ").trim();
  return flat.length <= max ? flat : `${flat.slice(0, max - 1).trimEnd()}…`;
}

/** A step's time for a list: whole milliseconds, or "under 1 ms" for the very quick ones. */
export function stepTime(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "n/a";
  return ms < 1 ? "under 1 ms" : `${Math.round(ms).toLocaleString("en-GB")} ms`;
}
