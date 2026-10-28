import { tokens } from "@/lib/format";
import { Empty } from "./ui";

type Line = { agent: string; day: string; runs: number; failed: number; input_tokens: number; output_tokens: number; organisation?: string };

export function UsageTable({ lines, withOrganisation = false }: { lines: Line[]; withOrganisation?: boolean }) {
  if (lines.length === 0) return <Empty title="No runs in this period" />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Day (UTC)</th>
            {withOrganisation ? <th scope="col">Organisation</th> : null}
            <th scope="col">Agent</th>
            <th scope="col" className="num">Runs</th>
            <th scope="col" className="num">Failed</th>
            <th scope="col" className="num">Tokens in</th>
            <th scope="col" className="num">Tokens out</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td>{l.day}</td>
              {withOrganisation ? <td>{l.organisation}</td> : null}
              <td>{l.agent}</td>
              <td className="num">{tokens(l.runs)}</td>
              <td className="num">{tokens(l.failed)}</td>
              <td className="num">{tokens(l.input_tokens)}</td>
              <td className="num">{tokens(l.output_tokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
