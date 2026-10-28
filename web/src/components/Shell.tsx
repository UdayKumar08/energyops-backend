"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import type { SiteListItem } from "@/api/types";
import type { NavGroup } from "@/lib/nav";
import { callSite } from "@/lib/client";

function withSite(href: string, site: string | null, keep: boolean): string {
  return keep && site ? `${href}?site=${encodeURIComponent(site)}` : href;
}

function Rail({
  groups,
  sites,
  organisation,
  name,
  role,
}: {
  groups: NavGroup[];
  sites: SiteListItem[];
  organisation: string | null;
  name: string;
  role: string;
}) {
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const current = params.get("site") ?? sites[0]?.slug ?? null;
  // On a narrow screen the menu is folded away so the page itself is the first thing seen.
  const [open, setOpen] = useState(false);

  async function signOut() {
    await callSite("DELETE", "/api/session");
    // A full page load on purpose: it starts from a clean state after the session changes.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }

  function pick(slug: string) {
    const next = new URLSearchParams(params.toString());
    next.set("site", slug);
    router.replace(`${pathname}?${next.toString()}`);
  }

  return (
    <aside className="flex flex-col gap-6 bg-ink px-4 py-6 text-paper md:sticky md:top-0 md:h-screen md:overflow-y-auto">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-lg font-bold leading-tight">EnergyOps</p>
          <p className="text-sm text-paper/70">{organisation ?? "Platform administration"}</p>
        </div>
        <button
          type="button"
          className="border border-paper/40 px-3 py-1 text-sm font-semibold md:hidden"
          aria-expanded={open}
          aria-controls="rail-menu"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Close menu" : "Menu"}
        </button>
      </div>

      {sites.length > 0 ? (
        <div>
          <label htmlFor="site-picker" className="mb-1 block text-sm font-semibold">
            Site
          </label>
          <select
            id="site-picker"
            className="field"
            style={{ background: "#1d363c", color: "#edf1ef", borderColor: "#4a6a68" }}
            value={current ?? ""}
            onChange={(e) => pick(e.target.value)}
          >
            {sites.map((s) => (
              <option key={s.slug} value={s.slug}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <nav aria-label="Main" id="rail-menu" className={`${open ? "flex" : "hidden"} flex-col gap-5 md:flex`}>
        {groups.map((group) => (
          <div key={group.title}>
            <p className="mb-1 text-sm font-semibold text-paper/70">{group.title}</p>
            <ul className="flex flex-col">
              {group.items.map((item) => {
                const active =
                  item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(item.href + "/");
                return (
                  <li key={item.href}>
                    <Link
                      href={withSite(item.href, current, item.keepSite)}
                      aria-current={active ? "page" : undefined}
                      className={`block border-l-4 px-3 py-1.5 text-[0.9375rem] ${
                        active
                          ? "border-amber bg-paper/10 font-semibold text-white"
                          : "border-transparent text-paper/85 hover:bg-paper/5"
                      }`}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className={`${open ? "block" : "hidden"} mt-auto border-t border-paper/20 pt-4 text-sm md:block`}>
        <p className="font-semibold">{name}</p>
        <p className="text-paper/70">{role.replaceAll("_", " ")}</p>
        <button type="button" onClick={signOut} className="mt-2 underline underline-offset-2 hover:text-white">
          Sign out
        </button>
      </div>
    </aside>
  );
}

export function Shell(props: React.ComponentProps<typeof Rail> & { children: React.ReactNode }) {
  const { children, ...rail } = props;
  return (
    <div className="grid min-h-screen md:grid-cols-[248px_1fr]">
      <Suspense fallback={<aside className="bg-ink" />}>
        <Rail {...rail} />
      </Suspense>
      <main className="min-w-0 px-4 py-8 md:px-10">
        <div className="mx-auto max-w-[1040px]">{children}</div>
      </main>
    </div>
  );
}
