import { Assistant } from "@/components/Assistant";
import { PageHeader } from "@/components/ui";
import { loadSites, pickSite, type SiteSearch } from "@/lib/sites";

export const metadata = { title: "Assistant" };

export default async function AssistantPage({ searchParams }: SiteSearch) {
  const { site: requested } = await searchParams;
  const site = pickSite(await loadSites(), requested);
  return (
    <>
      <PageHeader title="Assistant" lead="Ask in plain words, by typing or speaking." />
      <Assistant key={site?.slug ?? "none"} site={site?.slug ?? null} siteName={site?.name ?? null} />
    </>
  );
}
