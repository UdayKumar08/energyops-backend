import type { KnowledgeDocument, Me } from "@/api/types";
import { KnowledgeManager } from "@/components/KnowledgeManager";
import { Notice, PageHeader } from "@/components/ui";
import { backend } from "@/lib/backend";
import { settle } from "@/lib/safe";

export const metadata = { title: "Knowledge" };

export default async function KnowledgePage() {
  const me = await backend<Me>("/me");
  const docs = await settle(backend<KnowledgeDocument[]>("/knowledge/documents"));
  return (
    <>
      <PageHeader
        title="Knowledge"
        lead="Your organisation's own procedures and notes. Search shows the matching passages exactly as written, and never rewrites them."
      />
      {docs.error ? (
        <Notice tone="bad">{docs.error}</Notice>
      ) : (
        <KnowledgeManager documents={docs.data ?? []} canManage={me.permissions.includes("manage_knowledge")} />
      )}
    </>
  );
}
