import type { Tariff } from "@/api/types";
import { TariffForm } from "@/components/TariffForm";
import { Empty, PageHeader } from "@/components/ui";
import { backendOrNull } from "@/lib/backend";
import { loadSites, pickSite, type SiteSearch } from "@/lib/sites";

export const metadata = { title: "Tariff" };

export default async function TariffPage({ searchParams }: SiteSearch) {
  const { site: requested } = await searchParams;
  const site = pickSite(await loadSites(), requested);
  if (!site) return <><PageHeader title="Tariff" /><Empty title="No sites yet" /></>;
  const tariff = await backendOrNull<Tariff>(`/sites/${encodeURIComponent(site.slug)}/tariff`);
  return (
    <>
      <PageHeader
        title={`Tariff for ${site.name}`}
        lead="The prices behind every cost figure. Without a tariff the savings advisor stops instead of guessing a price."
      />
      <TariffForm key={site.slug} site={site.slug} initial={tariff} />
    </>
  );
}
