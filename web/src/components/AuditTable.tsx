import type { AuditEntry } from "@/api/types";
import { utcDateTime } from "@/lib/format";
import { Empty } from "./ui";

export function AuditTable({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) return <Empty title="Nothing recorded yet" />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">When (UTC)</th>
            <th scope="col">Who</th>
            <th scope="col">What</th>
            <th scope="col">On</th>
            <th scope="col">Detail</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={i}>
              <td className="whitespace-nowrap">{utcDateTime(e.at)}</td>
              <td>{e.actor ?? "system"}</td>
              <td className="font-semibold">{e.action}</td>
              <td className="break-all">{e.target ?? ""}</td>
              <td className="max-w-[28ch] break-words text-sm text-muted">
                {Object.entries(e.detail ?? {})
                  .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                  .join(", ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
