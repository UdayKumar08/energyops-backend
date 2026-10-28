import { describe, expect, it } from "vitest";
import { backendPathFor, isSameOrigin } from "@/lib/proxy-path";

const headers = (init: Record<string, string>) => ({
  get: (name: string) => init[name.toLowerCase()] ?? null,
});

describe("backendPathFor", () => {
  it("joins plain segments", () => {
    expect(backendPathFor(["sites", "demo-hotel", "forecast"])).toBe("/sites/demo-hotel/forecast");
  });
  it("refuses an empty path", () => {
    expect(backendPathFor([])).toBeNull();
  });
  it("keeps sign-in and service paths out of the proxy", () => {
    for (const first of ["auth", "health", "docs", "openapi.json", "redoc"]) {
      expect(backendPathFor([first, "x"])).toBeNull();
    }
  });
  it("refuses traversal and odd characters", () => {
    for (const bad of ["..", ".", "a/b", "a b", "a?x=1", "%2e%2e", "a#b", "a\\b", ""]) {
      expect(backendPathFor(["sites", bad])).toBeNull();
    }
  });
});

describe("isSameOrigin", () => {
  it("accepts the site's own origin", () => {
    expect(isSameOrigin(headers({ host: "app.example", origin: "https://app.example" }))).toBe(true);
  });
  it("refuses another site's origin, a malformed one and the null origin", () => {
    expect(isSameOrigin(headers({ host: "app.example", origin: "https://evil.example" }))).toBe(false);
    expect(isSameOrigin(headers({ host: "app.example", origin: "nonsense" }))).toBe(false);
    expect(isSameOrigin(headers({ host: "app.example", origin: "null" }))).toBe(false);
  });
  it("without Origin, relies on Sec-Fetch-Site", () => {
    expect(isSameOrigin(headers({ host: "a", "sec-fetch-site": "cross-site" }))).toBe(false);
    expect(isSameOrigin(headers({ host: "a", "sec-fetch-site": "same-origin" }))).toBe(true);
    expect(isSameOrigin(headers({ host: "a" }))).toBe(true);
  });
});
