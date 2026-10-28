"use client";

import { useState } from "react";
import type { UploadResult } from "@/api/types";
import { call, CallError, messageOf } from "@/lib/client";
import { utcDateTime } from "@/lib/format";
import { Notice } from "./ui";

const MAX_BYTES = 5 * 1024 * 1024;

export function UploadForm({ site, siteName }: { site: string; siteName: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(dryRun: boolean) {
    if (!file) return;
    if (file.size > MAX_BYTES) {
      setError("That file is larger than 5 MB. Split it into smaller files.");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const text = await file.text();
      const query = new URLSearchParams({ dry_run: String(dryRun), filename: file.name.slice(0, 200) });
      setResult(
        await call<UploadResult>("POST", `/sites/${encodeURIComponent(site)}/readings/upload?${query}`, {
          raw: { text, contentType: "text/csv" },
        }),
      );
    } catch (e) {
      if (e instanceof CallError && e.status === 422 && e.body && typeof e.body === "object" && "issues" in e.body) {
        setResult(e.body as UploadResult);
      } else {
        setError(messageOf(e));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="max-w-md">
        <label htmlFor="csv" className="label">
          CSV file for {siteName}
        </label>
        <input
          id="csv"
          type="file"
          accept=".csv,text/csv"
          className="field"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setResult(null);
            setError(null);
          }}
        />
        <p className="hint">
          Two columns with the header <code>timestamp,power_kw</code>. Timestamps need an offset, such as{" "}
          <code>2026-03-01T00:15:00Z</code>. Power is the average kW over the interval that starts at the timestamp.
        </p>
      </div>
      <div className="mt-4 flex gap-2">
        <button className="btn" type="button" disabled={!file || busy} onClick={() => send(true)}>
          Check the file
        </button>
        <button className="btn btn-primary" type="button" disabled={!file || busy} onClick={() => send(false)}>
          Upload readings
        </button>
      </div>

      <div className="mt-6" aria-live="polite">
        {error ? <Notice tone="bad">{error}</Notice> : null}
        {result ? (
          result.issue_count > 0 ? (
            <div>
              <Notice tone="bad">
                The file was not accepted and nothing was stored. {result.issue_count}{" "}
                {result.issue_count === 1 ? "problem" : "problems"} found
                {result.issue_count > result.issues.length ? `, the first ${result.issues.length} are listed` : ""}.
              </Notice>
              <div className="table-wrap mt-3">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Line</th>
                      <th scope="col">Problem</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.issues.map((i, n) => (
                      <tr key={n}>
                        <td>{i.line === 0 ? "File" : i.line}</td>
                        <td>{i.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <Notice>
              {result.accepted ? "Uploaded." : "The file looks fine. Nothing has been stored yet."} {result.rows.toLocaleString("en-GB")} readings
              {result.first && result.last ? ` from ${utcDateTime(result.first)} to ${utcDateTime(result.last)}` : ""}.
              {result.missing_intervals > 0 ? ` ${result.missing_intervals} intervals in that range have no reading.` : ""}
              {result.existing_in_range > 0
                ? ` ${result.existing_in_range.toLocaleString("en-GB")} readings are already stored in this time range, and any with the same time will be replaced.`
                : ""}
            </Notice>
          )
        ) : null}
      </div>
    </div>
  );
}
