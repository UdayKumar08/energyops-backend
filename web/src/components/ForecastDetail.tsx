import type { Forecast } from "@/api/types";
import { kw, kwh, percent, weekday } from "@/lib/format";
import { Notice, Pill } from "./ui";

type Days = Pick<Forecast, "days" | "backtest" | "backtest_note" | "limits" | "method" | "history_days">;

/** Daily forecast figures with the test of how the method did on days it had not seen. */
export function ForecastDetail({ forecast }: { forecast: Days }) {
  const test = forecast.backtest;
  return (
    <div>
      <div className="table-wrap">
        <table className="table">
          <caption className="sr-only">Forecast energy and peak demand for each day</caption>
          <thead>
            <tr>
              <th scope="col">Day (UTC)</th>
              <th scope="col" className="num">Energy</th>
              <th scope="col" className="num">Peak demand</th>
            </tr>
          </thead>
          <tbody>
            {forecast.days.map((d) => (
              <tr key={d.day}>
                <td>
                  {weekday(d.day)} {d.day}
                </td>
                <td className="num">{kwh(d.energy_kwh)}</td>
                <td className="num">{kw(d.peak_kw)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="mb-2 mt-6 font-semibold">How good is this forecast?</h3>
      {test ? (
        <div>
          <p className="max-w-[70ch]">
            The same method was built from the days before the last {test.days_tested} days of
            history and used to predict them. On average it was off by {kw(test.mae_kw)} in an
            interval ({percent(test.mae_percent_of_mean)} of the average demand), and its daily
            energy was off by {percent(test.daily_energy_error_percent)}.{" "}
            {test.beats_naive === null ? null : test.beats_naive ? (
              <>
                That is better than simply copying the same time a week earlier, which was off by{" "}
                {kw(test.naive_mae_kw ?? 0)}. <Pill tone="ok">beats the simple rule</Pill>
              </>
            ) : (
              <>
                That is <strong>not</strong> better than simply copying the same time a week earlier
                ({kw(test.naive_mae_kw ?? 0)}), so treat this forecast with caution.{" "}
                <Pill tone="warn">no better than the simple rule</Pill>
              </>
            )}
          </p>
        </div>
      ) : (
        <Notice tone="warn">{forecast.backtest_note ?? "The forecast has not been tested."}</Notice>
      )}
      <p className="mt-4 text-sm text-muted">{forecast.method} Built from {forecast.history_days} complete days.</p>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">
        {forecast.limits.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  );
}
