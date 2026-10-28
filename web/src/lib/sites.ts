import "server-only";
import type { SiteListItem } from "@/api/types";
import { backend } from "./backend";

export async function loadSites(): Promise<SiteListItem[]> {
  return backend<SiteListItem[]>("/sites");
}

/** The site in the address (?site=), or the first the person can see. Null when they have none. */
export function pickSite(sites: SiteListItem[], requested: string | undefined): SiteListItem | null {
  return sites.find((s) => s.slug === requested) ?? sites[0] ?? null;
}

export type SiteSearch = { searchParams: Promise<{ site?: string }> };
