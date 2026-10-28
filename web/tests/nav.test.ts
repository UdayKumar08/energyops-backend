import { describe, expect, it } from "vitest";
import { homeFor, navFor } from "@/lib/nav";

const labels = (permissions: string[]) =>
  navFor({ permissions, role: "x" }).flatMap((g) => g.items.map((i) => i.label));

describe("navFor", () => {
  it("gives a viewer only the read-only pages", () => {
    expect(labels(["view_data", "view_runs"])).toEqual(["Overview", "Forecast", "Tickets", "Knowledge"]);
  });
  it("adds the assistant and agent pages for roles that may run agents", () => {
    const items = labels(["view_data", "run_agents", "ask_assistant"]);
    expect(items).toContain("Assistant");
    expect(items).toContain("Reports");
    expect(items).toContain("Savings");
    expect(items).not.toContain("Upload readings");
  });
  it("shows the organisation pages only to those who manage them", () => {
    const items = labels(["view_data", "manage_users", "manage_sites", "view_org_audit"]);
    expect(items).toEqual(expect.arrayContaining(["People", "Sites", "Usage", "Audit log"]));
  });
  it("gives platform staff the platform pages and nothing of a customer's", () => {
    expect(labels(["admin_platform"])).toEqual(["Agents", "Organisations", "Usage", "Audit log"]);
    expect(homeFor({ permissions: ["admin_platform"] })).toBe("/admin/agents");
    expect(homeFor({ permissions: ["view_data"] })).toBe("/");
  });
  it("shows nothing for someone with no permissions", () => {
    expect(navFor({ permissions: [], role: "x" })).toEqual([]);
  });
});
