import { describe, expect, it } from "vitest";
import { hourlyRows, kw, kwh, percent, utcDate, utcDateTime, weekday } from "@/lib/format";

describe("format", () => {
  it("labels every time as UTC", () => {
    expect(utcDateTime("2026-03-04T02:15:00+00:00")).toBe("2026-03-04 02:15 UTC");
    expect(utcDateTime("2026-03-04T03:15:00+01:00")).toBe("2026-03-04 02:15 UTC");
    expect(utcDate("2026-03-04T23:59:00Z")).toBe("2026-03-04");
    expect(utcDateTime("not a date")).toBe("not a date");
  });
  it("formats power and energy with their units", () => {
    expect(kw(12.345)).toBe("12.3 kW");
    expect(kwh(1234.6)).toBe("1,235 kWh");
  });
  it("shows a missing percentage as n/a", () => {
    expect(percent(null)).toBe("n/a");
    expect(percent(3.84)).toBe("3.8%");
  });
  it("names the weekday of a UTC day", () => {
    expect(weekday("2026-03-02")).toBe("Mon");
  });
});

describe("hourlyRows", () => {
  it("averages readings into one row per UTC day with 24 hours", () => {
    const rows = hourlyRows([
      { ts: "2026-03-02T00:00:00Z", kw: 10 },
      { ts: "2026-03-02T00:30:00Z", kw: 20 },
      { ts: "2026-03-02T05:00:00Z", kw: 7 },
      { ts: "2026-03-01T23:00:00Z", kw: 3 },
    ]);
    expect(rows.map((r) => r.day)).toEqual(["2026-03-01", "2026-03-02"]);
    expect(rows[1]?.hours[0]).toBe(15);
    expect(rows[1]?.hours[5]).toBe(7);
    expect(rows[1]?.hours[1]).toBeNull();
    expect(rows[0]?.hours).toHaveLength(24);
  });
  it("skips timestamps it cannot read", () => {
    expect(hourlyRows([{ ts: "nope", kw: 1 }])).toEqual([]);
  });
});
