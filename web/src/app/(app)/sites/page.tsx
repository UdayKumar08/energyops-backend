import { PageHeader, Section } from "@/components/ui";
import { SiteManager } from "@/components/SiteManager";
import { loadSites } from "@/lib/sites";
import { utcDateTime } from "@/lib/format";
import { Pill } from "@/components/ui";

export const metadata = { title: "Sites" };

export default async function SitesPage() {
  const sites = await loadSites();
  return (
    <>
      <PageHeader title="Sites" lead="The buildings whose meters you track." />
      <Section title="Your sites">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Site</th>
                <th scope="col">Readings</th>
                <th scope="col">Tariff</th>
              </tr>
            </thead>
            <tbody>
              {sites.map((s) => (
                <tr key={s.slug}>
                  <td>
                    {s.name}
                    <span className="block text-sm text-muted">{s.slug}</span>
                  </td>
                  <td>
                    {s.first_reading && s.last_reading
                      ? `${utcDateTime(s.first_reading)} to ${utcDateTime(s.last_reading)}`
                      : "None yet"}
                  </td>
                  <td>{s.has_tariff ? <Pill tone="ok">set</Pill> : <Pill tone="warn">missing</Pill>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
      <Section title="Add a site">
        <SiteManager />
      </Section>
    </>
  );
}
