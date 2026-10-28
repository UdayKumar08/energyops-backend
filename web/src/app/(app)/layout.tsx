import { redirect } from "next/navigation";
import type { Me, SiteListItem } from "@/api/types";
import { Shell } from "@/components/Shell";
import { backend } from "@/lib/backend";
import { navFor } from "@/lib/nav";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await backend<Me>("/me");
  const sites = me.permissions.includes("view_data") ? await backend<SiteListItem[]>("/sites") : [];
  const groups = navFor(me);
  if (groups.length === 0) redirect("/login");
  return (
    <Shell groups={groups} sites={sites} organisation={me.organisation} name={me.name} role={me.role}>
      {children}
    </Shell>
  );
}
