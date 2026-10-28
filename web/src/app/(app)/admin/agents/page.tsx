import Link from "next/link";
import type { AdminAgentRow } from "@/api/types";
import { Notice, PageHeader, StatusPill } from "@/components/ui";
import { backend } from "@/lib/backend";
import { settle } from "@/lib/safe";

export const metadata = { title: "Agents" };

export default async function AgentsPage() {
  const agents = await settle(backend<AdminAgentRow[]>("/admin/agents"));
  return (
    <>
      <PageHeader
        title="Agents"
        lead="The registry of agents on this installation. Customers can only use an agent that is active."
      />
      {agents.error ? (
        <Notice tone="bad">{agents.error}</Notice>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Agent</th>
                <th scope="col">Status</th>
                <th scope="col">Model</th>
                <th scope="col">Prompt</th>
                <th scope="col">Settings</th>
              </tr>
            </thead>
            <tbody>
              {(agents.data ?? []).map((a) => (
                <tr key={a.summary.id}>
                  <td>
                    <Link className="font-semibold underline underline-offset-2" href={`/admin/agents/${a.summary.id}`}>
                      {a.summary.name}
                    </Link>
                    <span className="block max-w-[50ch] text-sm text-muted">{a.summary.description}</span>
                  </td>
                  <td>
                    <StatusPill status={a.summary.status} />
                  </td>
                  <td>
                    {a.summary.model_name}
                    <span className="block text-sm text-muted">{a.summary.model_provider}</span>
                  </td>
                  <td>{a.summary.prompt_version}</td>
                  <td>{Object.keys(a.settings).length > 0 ? "Changed from built-in" : "Built-in"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
