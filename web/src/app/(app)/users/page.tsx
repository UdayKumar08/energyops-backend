import type { Me, OrgPerson, SiteListItem } from "@/api/types";
import { PeopleManager } from "@/components/PeopleManager";
import { Notice, PageHeader } from "@/components/ui";
import { backend } from "@/lib/backend";
import { settle } from "@/lib/safe";

export const metadata = { title: "People" };

export default async function UsersPage() {
  const me = await backend<Me>("/me");
  const [people, sites] = await Promise.all([
    settle(backend<OrgPerson[]>("/users")),
    backend<SiteListItem[]>("/sites"),
  ]);
  return (
    <>
      <PageHeader title="People" lead="Who can sign in to your organisation, what they may do, and which sites they can see." />
      {people.error ? (
        <Notice tone="bad">{people.error}</Notice>
      ) : (
        <PeopleManager people={people.data ?? []} sites={sites} meId={me.user_id} />
      )}
    </>
  );
}
