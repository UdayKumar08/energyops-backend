"use client";

import { useState } from "react";
import { RunPanel } from "./RunPanel";

export function ReportForm({ site }: { site: string }) {
  const [audience, setAudience] = useState("manager");
  return (
    <div>
      <div className="mb-4 max-w-xs">
        <label htmlFor="audience" className="label">
          Who is it for?
        </label>
        <select id="audience" className="field" value={audience} onChange={(e) => setAudience(e.target.value)}>
          <option value="manager">A manager</option>
          <option value="technical">A technician</option>
        </select>
      </div>
      <RunPanel key={audience} agentId="report-writer" site={site} label="Write the report" body={{ audience }} />
    </div>
  );
}
