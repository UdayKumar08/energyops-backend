import { describe, expect, it } from "vitest";
import type { RunSummary } from "@/api/types";
import { agentName, agentsIn, filterRuns, readFilter, shorten, stepTime, took } from "@/lib/runs";

const run = (over: Partial<RunSummary>): RunSummary => ({
  run_id: "r",
  agent: "savings-advisor",
  status: "succeeded",
  question: "q",
  model_provider: "p",
  model_name: "m",
  attempts: 1,
  error_kind: null,
  started_at: "2026-10-01T10:00:00Z",
  finished_at: "2026-10-01T10:00:02Z",
  ...over,
});

describe("readFilter", () => {
  it("reads agent and status from the address", () => {
    expect(readFilter({ agent: "savings-advisor", status: "failed" })).toEqual({
      agent: "savings-advisor",
      status: "failed",
    });
  });
  it("ignores a status it does not know and takes the first of repeated values", () => {
    expect(readFilter({ agent: ["a", "b"], status: "weird" })).toEqual({ agent: "a", status: "" });
  });
  it("gives no filter for an empty address", () => {
    expect(readFilter({})).toEqual({ agent: "", status: "" });
  });
  it("cuts an over-long agent value", () => {
    expect(readFilter({ agent: "x".repeat(300) }).agent).toHaveLength(64);
  });
});

describe("filterRuns", () => {
  const runs = [
    run({ run_id: "1" }),
    run({ run_id: "2", status: "failed" }),
    run({ run_id: "3", agent: "report-writer" }),
  ];
  it("keeps everything with no filter", () => {
    expect(filterRuns(runs, { agent: "", status: "" })).toHaveLength(3);
  });
  it("filters by agent, by status and by both", () => {
    expect(filterRuns(runs, { agent: "report-writer", status: "" }).map((r) => r.run_id)).toEqual(["3"]);
    expect(filterRuns(runs, { agent: "", status: "failed" }).map((r) => r.run_id)).toEqual(["2"]);
    expect(filterRuns(runs, { agent: "savings-advisor", status: "failed" }).map((r) => r.run_id)).toEqual(["2"]);
  });
});

describe("agentsIn", () => {
  it("lists each agent once, sorted", () => {
    expect(agentsIn([run({ agent: "b" }), run({ agent: "a" }), run({ agent: "b" })])).toEqual(["a", "b"]);
  });
});

describe("took", () => {
  it("reads in milliseconds, seconds and minutes", () => {
    expect(took("2026-10-01T10:00:00Z", "2026-10-01T10:00:00.250Z")).toBe("250 ms");
    expect(took("2026-10-01T10:00:00Z", "2026-10-01T10:00:02.500Z")).toBe("2.5 s");
    expect(took("2026-10-01T10:00:00Z", "2026-10-01T10:02:05Z")).toBe("2 min 5 s");
  });
  it("says n/a for times that do not make sense", () => {
    expect(took("nope", "2026-10-01T10:00:00Z")).toBe("n/a");
    expect(took("2026-10-01T10:00:05Z", "2026-10-01T10:00:00Z")).toBe("n/a");
  });
});

describe("agentName and shorten", () => {
  it("makes an agent id readable", () => {
    expect(agentName("savings-advisor")).toBe("Savings advisor");
    expect(agentName("anomaly_investigator")).toBe("Anomaly investigator");
  });
  it("leaves a name that already reads well alone", () => {
    expect(agentName("EnergyOps Savings Advisor")).toBe("EnergyOps Savings Advisor");
  });
  it("cuts only long questions and flattens line breaks", () => {
    expect(shorten("short\nquestion")).toBe("short question");
    expect(shorten("a".repeat(200), 20)).toHaveLength(20);
    expect(shorten("a".repeat(200), 20).endsWith("…")).toBe(true);
  });
});

describe("stepTime", () => {
  it("rounds and handles tiny or odd values", () => {
    expect(stepTime(0.019)).toBe("under 1 ms");
    expect(stepTime(71.933)).toBe("72 ms");
    expect(stepTime(1500)).toBe("1,500 ms");
    expect(stepTime(-1)).toBe("n/a");
  });
});
