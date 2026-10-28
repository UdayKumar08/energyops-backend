import { RunPanel } from "@/components/RunPanel";
import { Empty, Notice, PageHeader } from "@/components/ui";
import { loadSites, pickSite, type SiteSearch } from "@/lib/sites";

export const metadata = { title: "Savings" };

export default async function SavingsPage({ searchParams }: SiteSearch) {
  const { site: requested } = await searchParams;
  const site = pickSite(await loadSites(), requested);
  if (!site) return <><PageHeader title="Savings" /><Empty title="No sites yet" /></>;
  return (
    <>
      <PageHeader
        title="Savings"
        lead={`Where the cost of running ${site.name} might come down, with estimated ranges worked out from the site's tariff.`}
      />
      <div className="mb-6 max-w-[70ch]">
        <Notice tone="warn">
          These are estimates, not promises. They are calculated from past readings and the tariff
          saved for the site, and nothing here has been measured as saved.
        </Notice>
      </div>
      <RunPanel agentId="savings-advisor" site={site.slug} label="Find where to save" key={site.slug} />
    </>
  );
}
