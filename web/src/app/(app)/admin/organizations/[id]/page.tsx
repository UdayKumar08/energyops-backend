import Link from "next/link";
import type { AdminPerson, Organisation } from "@/api/types";
import { Notice, Pill, PageHeader } from "@/components/ui";
import { backend } from "@/lib/backend";
import { settle } from "@/lib/safe";

export default async function OrganisationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [orgs, people] = await Promise.all([
    settle(backend<Organisation[]>("/admin/organizations")),
    settle(backend<AdminPerson[]>(`/admin/organizations/${encodeURIComponent(id)}/users`)),
  ]);
  const org = orgs.data?.find((o) => o.id === id);
  return (
    <>
      <PageHeader
        title={org?.name ?? "Organisation"}
        lead={
          <Link className="underline underline-offset-2" href="/admin/organizations">
            All organisations
          </Link>
        }
      />
      {people.error ? (
        <Notice tone="bad">{people.error}</Notice>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <caption className="sr-only">People in this organisation</caption>
            <thead>
              <tr>
                <th scope="col">Person</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {(people.data ?? []).map((p) => (
                <tr key={p.id}>
                  <td>
                    {p.name}
                    <span className="block text-sm text-muted">{p.email}</span>
                  </td>
                  <td>{p.role.replaceAll("_", " ")}</td>
                  <td>{p.is_active ? <Pill tone="ok">active</Pill> : <Pill tone="bad">deactivated</Pill>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
