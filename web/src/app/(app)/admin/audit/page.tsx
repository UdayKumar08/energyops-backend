import type { AuditEntry } from "@/api/types";
import { AuditTable } from "@/components/AuditTable";
import { Notice, PageHeader } from "@/components/ui";
import { backend } from "@/lib/backend";
import { settle } from "@/lib/safe";

export const metadata = { title: "Platform audit log" };

export default async function PlatformAuditPage() {
  const audit = await settle(backend<AuditEntry[]>("/admin/audit?limit=200"));
  return (
    <>
      <PageHeader title="Audit log" lead="Platform-level changes: organisations created, agents changed. Newest first." />
      {audit.error ? <Notice tone="bad">{audit.error}</Notice> : <AuditTable entries={audit.data ?? []} />}
    </>
  );
}
