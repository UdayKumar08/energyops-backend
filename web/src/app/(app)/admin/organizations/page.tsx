import type { Organisation } from "@/api/types";
import { OrganisationManager } from "@/components/OrganisationManager";
import { Notice, PageHeader } from "@/components/ui";
import { backend } from "@/lib/backend";
import { settle } from "@/lib/safe";

export const metadata = { title: "Organisations" };

export default async function OrganisationsPage() {
  const orgs = await settle(backend<Organisation[]>("/admin/organizations"));
  return (
    <>
      <PageHeader
        title="Organisations"
        lead="The customers on this installation. Platform staff create organisations and their first administrator, but cannot see an organisation's sites, readings or runs."
      />
      {orgs.error ? <Notice tone="bad">{orgs.error}</Notice> : <OrganisationManager organisations={orgs.data ?? []} />}
    </>
  );
}
