/** Number and time formatting. All times in this product are UTC and are labelled as such. */

export function kw(value: number): string {
  return `${value.toLocaleString("en-GB", { maximumFractionDigits: 1 })} kW`;
}

export function kwh(value: number): string {
  return `${value.toLocaleString("en-GB", { maximumFractionDigits: 0 })} kWh`;
}

export function utcDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const day = date.toISOString().slice(0, 10);
  const time = date.toISOString().slice(11, 16);
  return `${day} ${time} UTC`;
}

export function utcDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toISOString().slice(0, 10);
}

export function weekday(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? day
    : date.toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
}

export function tokens(value: number): string {
  return value.toLocaleString("en-GB");
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "n/a" : `${value.toFixed(1)}%`;
}

/** Readings in, one row per UTC day with 24 hourly averages (null where there is no data). */
export function hourlyRows(
  points: ReadonlyArray<{ ts: string; kw: number }>,
): { day: string; hours: (number | null)[] }[] {
  const days = new Map<string, { sum: number[]; count: number[] }>();
  for (const point of points) {
    const date = new Date(point.ts);
    if (Number.isNaN(date.getTime())) continue;
    const day = date.toISOString().slice(0, 10);
    const hour = date.getUTCHours();
    const entry = days.get(day) ?? { sum: Array<number>(24).fill(0), count: Array<number>(24).fill(0) };
    entry.sum[hour] = (entry.sum[hour] ?? 0) + point.kw;
    entry.count[hour] = (entry.count[hour] ?? 0) + 1;
    days.set(day, entry);
  }
  return [...days.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([day, { sum, count }]) => ({
      day,
      hours: sum.map((total, hour) => ((count[hour] ?? 0) > 0 ? total / (count[hour] ?? 1) : null)),
    }));
}
