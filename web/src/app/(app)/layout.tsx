import { redirect } from "next/navigation";
import type { Me, SiteListItem } from "@/api/types";
import { Shell } from "@/components/Shell";
import { ApiError, backend } from "@/lib/backend";
import { navFor } from "@/lib/nav";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  let me: Me;
  try {
    me = await backend<Me>("/me");
  } catch (error) {
    // A valid Microsoft sign-in for someone EnergyOps does not know, or has switched off.
    if (error instanceof ApiError && error.status === 403) redirect("/login?denied=1");
    throw error;
  }
  const sites = me.permissions.includes("view_data") ? await backend<SiteListItem[]>("/sites") : [];
  const groups = navFor(me);
  if (groups.length === 0) redirect("/login");
  return (
    <Shell groups={groups} sites={sites} organisation={me.organisation} name={me.name} role={me.role}>
      {children}
    </Shell>
  );
}
