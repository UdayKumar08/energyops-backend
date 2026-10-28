import { hourlyRows, kw, weekday } from "@/lib/format";

type Mark = { day: string; fromHour: number; toHour: number; label: string };

/**
 * One row for each day, one cell for each hour, darker where demand is higher. It shows a week of
 * a building's shape at a glance: the night base load, the working-day hump, the quieter
 * weekend. Amber ticks under a day mark events the detector found. Forecast days are drawn with
 * a dashed outline so they cannot be mistaken for measurements.
 */
export function LoadStrip({
  points,
  marks = [],
  forecast = false,
  max,
  caption,
}: {
  points: ReadonlyArray<{ ts: string; kw: number }>;
  marks?: Mark[];
  forecast?: boolean;
  max?: number;
  caption: string;
}) {
  const rows = hourlyRows(points);
  if (rows.length === 0) return null;
  const top = max ?? Math.max(1, ...rows.flatMap((r) => r.hours.filter((h): h is number => h !== null)));
  const cell = 28;
  const gap = 2;
  const left = 56;
  const rowHeight = 22;
  const width = left + 24 * (cell + gap);
  const height = 28 + rows.length * (rowHeight + gap);

  return (
    <figure>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={caption}
        className="h-auto w-full max-w-[960px]"
      >
        {[0, 6, 12, 18].map((hour) => (
          <text key={hour} x={left + hour * (cell + gap)} y={12} fontSize={11} fill="var(--color-muted)">
            {String(hour).padStart(2, "0")}:00
          </text>
        ))}
        {rows.map((row, r) => {
          const y = 20 + r * (rowHeight + gap);
          return (
            <g key={row.day}>
              <text x={0} y={y + 15} fontSize={12} fill="var(--color-ink)">
                {weekday(row.day)} {row.day.slice(8)}
              </text>
              {row.hours.map((value, h) => {
                const x = left + h * (cell + gap);
                if (value === null) {
                  return (
                    <rect key={h} x={x} y={y} width={cell} height={rowHeight} fill="none" stroke="var(--color-line)" strokeDasharray="2 3" />
                  );
                }
                const strength = Math.min(1, value / top);
                return (
                  <rect
                    key={h}
                    x={x}
                    y={y}
                    width={cell}
                    height={rowHeight}
                    fill="var(--color-teal)"
                    fillOpacity={0.08 + 0.92 * strength}
                    stroke={forecast ? "var(--color-teal-deep)" : "none"}
                    strokeDasharray={forecast ? "3 2" : undefined}
                  >
                    <title>{`${row.day} ${String(h).padStart(2, "0")}:00 UTC, ${kw(value)}`}</title>
                  </rect>
                );
              })}
              {marks
                .filter((m) => m.day === row.day)
                .map((m, i) => (
                  <rect
                    key={i}
                    x={left + m.fromHour * (cell + gap)}
                    y={y + rowHeight - 4}
                    width={Math.max(cell, (m.toHour - m.fromHour + 1) * (cell + gap) - gap)}
                    height={4}
                    fill="var(--color-amber)"
                  >
                    <title>{m.label}</title>
                  </rect>
                ))}
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-2 text-sm text-muted">
        {caption} Darker cells are higher demand, up to {kw(top)}.
        {marks.length > 0 ? " Amber marks are events the detector found." : ""}
        {forecast ? " Dashed outlines are forecast, not measured." : ""}
      </figcaption>
    </figure>
  );
}
