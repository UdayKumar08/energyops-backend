import Link from "next/link";
import { notFound } from "next/navigation";
import type { AdminAgentDetail, ProviderInfo } from "@/api/types";
import { AgentEditor } from "@/components/AgentEditor";
import { PageHeader, StatusPill } from "@/components/ui";
import { ApiError, backend } from "@/lib/backend";

export default async function AgentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let detail: AdminAgentDetail;
  try {
    detail = await backend<AdminAgentDetail>(`/admin/agents/${encodeURIComponent(id)}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
  const providers = await backend<ProviderInfo[]>("/admin/providers");
  return (
    <>
      <PageHeader
        title={detail.summary.name}
        lead={
          <>
            <Link className="underline underline-offset-2" href="/admin/agents">
              All agents
            </Link>{" "}
            <StatusPill status={detail.summary.status} />
          </>
        }
      />
      <AgentEditor detail={detail} providers={providers} />
    </>
  );
}
