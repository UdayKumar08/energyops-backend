import type { Forecast } from "@/api/types";
import { ForecastDetail } from "@/components/ForecastDetail";
import { LoadStrip } from "@/components/LoadStrip";
import { Empty, Notice, PageHeader, Section } from "@/components/ui";
import { backend } from "@/lib/backend";
import { settle } from "@/lib/safe";
import { loadSites, pickSite, type SiteSearch } from "@/lib/sites";

export const metadata = { title: "Forecast" };

export default async function ForecastPage({ searchParams }: SiteSearch) {
  const { site: requested } = await searchParams;
  const site = pickSite(await loadSites(), requested);
  if (!site) return <><PageHeader title="Forecast" /><Empty title="No sites yet" /></>;
  const result = await settle(backend<Forecast>(`/sites/${encodeURIComponent(site.slug)}/forecast?days=7`));
  const points = (result.data?.points ?? []).map((p) => ({ ts: p.timestamp, kw: p.power_kw }));
  return (
    <>
      <PageHeader
        title={`Forecast for ${site.name}`}
        lead="The next seven days, from the pattern of this site's own past weeks."
      />
      {result.error ? (
        <Notice tone="warn">
          No forecast can be made yet. {result.error}
        </Notice>
      ) : result.data ? (
        <>
          <Section title="The next seven days">
            <LoadStrip
              points={points}
              forecast
              caption="Forecast average demand for each hour of the next seven days, in UTC."
            />
          </Section>
          <Section title="Daily figures and accuracy">
            <ForecastDetail forecast={result.data} />
          </Section>
        </>
      ) : null}
    </>
  );
}
