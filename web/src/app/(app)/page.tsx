import { redirect } from "next/navigation";
import type { AnomalyList, FactSheet, Me, ReadingPoint } from "@/api/types";
import { EventTable } from "@/components/EventTable";
import { LoadStrip } from "@/components/LoadStrip";
import { Empty, Notice, PageHeader, Section } from "@/components/ui";
import { backend } from "@/lib/backend";
import { utcDateTime } from "@/lib/format";
import { homeFor } from "@/lib/nav";
import { settle } from "@/lib/safe";
import { loadSites, pickSite, type SiteSearch } from "@/lib/sites";

export const metadata = { title: "Overview" };

export default async function Overview({ searchParams }: SiteSearch) {
  const me = await backend<Me>("/me");
  if (!me.permissions.includes("view_data")) redirect(homeFor(me));
  const { site: requested } = await searchParams;
  const site = pickSite(await loadSites(), requested);
  if (!site) {
    return (
      <>
        <PageHeader title="Overview" />
        <Empty title="No sites yet">
          {me.permissions.includes("manage_sites")
            ? "Add a site under Organisation, then upload its readings."
            : "Ask an administrator to give you access to a site."}
        </Empty>
      </>
    );
  }
  const slug = encodeURIComponent(site.slug);
  const [sheet, series, events] = await Promise.all([
    settle(backend<FactSheet>(`/sites/${slug}/summary`)),
    settle(backend<ReadingPoint[]>(`/sites/${slug}/series?days=7`)),
    settle(backend<AnomalyList>(`/sites/${slug}/anomalies`)),
  ]);

  const marks = (events.data?.anomalies ?? []).flatMap((e) => {
    const start = new Date(e.start);
    const end = new Date(e.end);
    if (start.toISOString().slice(0, 10) !== end.toISOString().slice(0, 10)) {
      return [{ day: start.toISOString().slice(0, 10), fromHour: start.getUTCHours(), toHour: 23, label: `${e.id}: ${e.kind}` }];
    }
    return [
      { day: start.toISOString().slice(0, 10), fromHour: start.getUTCHours(), toHour: end.getUTCHours(), label: `${e.id}: ${e.kind}` },
    ];
  });

  return (
    <>
      <PageHeader
        title={site.name}
        lead={
          site.last_reading
            ? `Readings from ${utcDateTime(site.first_reading ?? site.last_reading)} to ${utcDateTime(site.last_reading)}.`
            : "No readings yet."
        }
      />

      <Section title="The last seven days">
        {series.error ? (
          <Notice tone="bad">{series.error}</Notice>
        ) : series.data && series.data.length > 0 ? (
          <LoadStrip
            points={series.data}
            marks={marks}
            caption="Average demand for each hour of the last seven days in the readings, in UTC."
          />
        ) : (
          <Empty title="No readings to show">Upload a CSV file of readings to see this site&apos;s week.</Empty>
        )}
      </Section>

      <Section
        title="Key figures"
        note="Each figure is calculated from the meter readings by the product, not by a model. The tag is the fact's id, which agents cite."
      >
        {sheet.error ? (
          <Notice tone="bad">{sheet.error}</Notice>
        ) : sheet.data ? (
          <>
            <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
              {sheet.data.facts.map((f) => (
                <div key={f.id} className="flex items-baseline justify-between gap-4 border-b border-line pb-2">
                  <dt className="text-sm text-muted">
                    {f.description}
                    <span className="ml-2 rounded border border-line px-1 text-xs">{f.id}</span>
                  </dt>
                  <dd className="figure text-lg font-semibold">{f.display}</dd>
                </div>
              ))}
            </dl>
            {sheet.data.warnings.map((w) => (
              <p key={w} className="mt-3 text-sm text-amber">
                {w}
              </p>
            ))}
          </>
        ) : null}
      </Section>

      <Section
        title="Unusual events"
        note={events.data?.note}
      >
        {events.error ? (
          <Notice tone="bad">{events.error}</Notice>
        ) : (
          <EventTable
            site={site.slug}
            events={events.data?.anomalies ?? []}
            canInvestigate={me.permissions.includes("run_agents")}
            canDraft={me.permissions.includes("draft_tickets")}
          />
        )}
      </Section>
    </>
  );
}
