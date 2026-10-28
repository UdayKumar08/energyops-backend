import type { Me } from "@/api/types";

export type NavItem = { href: string; label: string; keepSite: boolean };
export type NavGroup = { title: string; items: NavItem[] };

/** The menu for a person, from the permissions the backend reports for them. The backend still
 * checks every request; this only decides what is worth showing. */
export function navFor(me: Pick<Me, "permissions" | "role">): NavGroup[] {
  const can = (p: string) => me.permissions.includes(p);
  const groups: NavGroup[] = [];

  if (can("admin_platform")) {
    groups.push({
      title: "Platform",
      items: [
        { href: "/admin/agents", label: "Agents", keepSite: false },
        { href: "/admin/organizations", label: "Organisations", keepSite: false },
        { href: "/admin/usage", label: "Usage", keepSite: false },
        { href: "/admin/audit", label: "Audit log", keepSite: false },
      ],
    });
  }

  const operate: NavItem[] = [];
  if (can("view_data")) operate.push({ href: "/", label: "Overview", keepSite: true });
  if (can("ask_assistant")) operate.push({ href: "/assistant", label: "Assistant", keepSite: true });
  if (can("view_data")) operate.push({ href: "/forecast", label: "Forecast", keepSite: true });
  if (can("run_agents")) {
    operate.push({ href: "/reports", label: "Reports", keepSite: true });
    operate.push({ href: "/savings", label: "Savings", keepSite: true });
  }
  if (can("view_data")) operate.push({ href: "/tickets", label: "Tickets", keepSite: false });
  if (operate.length) groups.push({ title: "Operate", items: operate });

  const data: NavItem[] = [];
  if (can("upload_data")) data.push({ href: "/data", label: "Upload readings", keepSite: true });
  if (can("manage_tariffs")) data.push({ href: "/tariff", label: "Tariff", keepSite: true });
  if (can("view_data")) data.push({ href: "/knowledge", label: "Knowledge", keepSite: false });
  if (data.length) groups.push({ title: "Data", items: data });

  const organisation: NavItem[] = [];
  if (can("manage_users")) organisation.push({ href: "/users", label: "People", keepSite: false });
  if (can("manage_sites")) organisation.push({ href: "/sites", label: "Sites", keepSite: false });
  if (can("view_org_audit")) {
    organisation.push({ href: "/usage", label: "Usage", keepSite: false });
    organisation.push({ href: "/audit", label: "Audit log", keepSite: false });
  }
  if (organisation.length) groups.push({ title: "Organisation", items: organisation });

  return groups;
}

export function homeFor(me: Pick<Me, "permissions">): string {
  return me.permissions.includes("view_data") ? "/" : "/admin/agents";
}
