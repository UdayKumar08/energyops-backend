import { ReportForm } from "@/components/ReportForm";
import { Empty, PageHeader } from "@/components/ui";
import { loadSites, pickSite, type SiteSearch } from "@/lib/sites";

export const metadata = { title: "Reports" };

export default async function ReportsPage({ searchParams }: SiteSearch) {
  const { site: requested } = await searchParams;
  const site = pickSite(await loadSites(), requested);
  if (!site) return <><PageHeader title="Reports" /><Empty title="No sites yet" /></>;
  return (
    <>
      <PageHeader
        title="Reports"
        lead={`A short written report on ${site.name}. Every figure in it was checked against the calculated facts before you see it.`}
      />
      <ReportForm site={site.slug} key={site.slug} />
    </>
  );
}
