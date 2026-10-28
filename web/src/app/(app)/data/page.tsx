import type { Ingestion } from "@/api/types";
import { Empty, Notice, PageHeader, Section } from "@/components/ui";
import { UploadForm } from "@/components/UploadForm";
import { backend } from "@/lib/backend";
import { utcDateTime } from "@/lib/format";
import { settle } from "@/lib/safe";
import { loadSites, pickSite, type SiteSearch } from "@/lib/sites";

export const metadata = { title: "Upload readings" };

export default async function DataPage({ searchParams }: SiteSearch) {
  const { site: requested } = await searchParams;
  const site = pickSite(await loadSites(), requested);
  const history = await settle(backend<Ingestion[]>("/ingestions"));
  return (
    <>
      <PageHeader
        title="Upload readings"
        lead="Add meter readings from a CSV file. A file is accepted whole or not at all, so a mistake never leaves half a file behind."
      />
      {site ? (
        <Section title="Send a file" note="Use Check the file first to see what an upload would do without storing anything.">
          <UploadForm key={site.slug} site={site.slug} siteName={site.name} />
        </Section>
      ) : (
        <Empty title="No sites yet">Add a site first.</Empty>
      )}
      <Section title="Earlier uploads">
        {history.error ? (
          <Notice tone="bad">{history.error}</Notice>
        ) : (history.data ?? []).length === 0 ? (
          <Empty title="Nothing uploaded yet" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Uploaded (UTC)</th>
                  <th scope="col">Site</th>
                  <th scope="col">File</th>
                  <th scope="col" className="num">Readings</th>
                  <th scope="col">Covers</th>
                </tr>
              </thead>
              <tbody>
                {(history.data ?? []).map((i) => (
                  <tr key={i.id}>
                    <td>{utcDateTime(i.uploaded_at)}</td>
                    <td>{i.site}</td>
                    <td>{i.filename ?? "not named"}</td>
                    <td className="num">{i.rows.toLocaleString("en-GB")}</td>
                    <td>
                      {utcDateTime(i.first)} to {utcDateTime(i.last)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}
