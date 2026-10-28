"use client";

import { useState } from "react";
import type { RunRecord } from "@/api/types";
import { call, messageOf } from "@/lib/client";
import { RunResult } from "./RunView";
import { Notice } from "./ui";

/** A button that runs one agent for one site and shows the checked answer. */
export function RunPanel({
  agentId,
  site,
  label,
  body = {},
}: {
  agentId: string;
  site: string;
  label: string;
  body?: Record<string, unknown>;
}) {
  const [run, setRun] = useState<RunRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      setRun(await call<RunRecord>("POST", `/agents/${agentId}/runs`, { body: { site, ...body } }));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <button className="btn btn-primary" onClick={go} disabled={busy} type="button">
        {busy ? "Working" : label}
      </button>
      <div aria-live="polite" className="mt-4">
        {error ? <Notice tone="bad">{error}</Notice> : null}
        {run ? <RunResult run={run} /> : null}
      </div>
    </div>
  );
}
