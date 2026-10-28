"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Organisation } from "@/api/types";
import { call, messageOf } from "@/lib/client";
import { utcDate } from "@/lib/format";
import { Field, Notice, Pill } from "./ui";

export function OrganisationManager({ organisations }: { organisations: Organisation[] }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [adminName, setAdminName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await call("POST", "/admin/organizations", {
        body: { name, slug, admin_name: adminName, admin_email: adminEmail },
      });
      setMessage({ tone: "ok", text: `${name} was created. ${adminName} can sign in as its administrator.` });
      setName("");
      setSlug("");
      setAdminName("");
      setAdminEmail("");
      router.refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  }

  async function toggle(org: Organisation) {
    setBusy(true);
    setMessage(null);
    try {
      await call("PATCH", `/admin/organizations/${org.id}`, { body: { is_active: !org.is_active } });
      router.refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-10">
      <div className="table-wrap">
        <table className="table">
          <caption className="sr-only">Organisations</caption>
          <thead>
            <tr>
              <th scope="col">Organisation</th>
              <th scope="col" className="num">People</th>
              <th scope="col">Created</th>
              <th scope="col">Status</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {organisations.map((o) => (
              <tr key={o.id}>
                <td>
                  <Link className="font-semibold underline underline-offset-2" href={`/admin/organizations/${o.id}`}>
                    {o.name}
                  </Link>
                  <span className="block text-sm text-muted">{o.slug}</span>
                </td>
                <td className="num">{o.people}</td>
                <td>{utcDate(o.created_at)}</td>
                <td>{o.is_active ? <Pill tone="ok">active</Pill> : <Pill tone="bad">suspended</Pill>}</td>
                <td>
                  <button className={`btn btn-small ${o.is_active ? "btn-danger" : ""}`} type="button" disabled={busy} onClick={() => toggle(o)}>
                    {o.is_active ? "Suspend" : "Reactivate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <section aria-labelledby="new-org">
        <h2 id="new-org" className="mb-3 text-xl font-semibold">
          Create an organisation
        </h2>
        <form onSubmit={add} className="grid max-w-3xl gap-x-6 md:grid-cols-2">
          <Field label="Organisation name" htmlFor="o-name">
            <input id="o-name" className="field" value={name} maxLength={200} required onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Short name" htmlFor="o-slug" hint="Lower case letters, numbers and hyphens. It cannot be changed later.">
            <input id="o-slug" className="field" value={slug} maxLength={64} required pattern="[a-z0-9]+(-[a-z0-9]+)*" onChange={(e) => setSlug(e.target.value.toLowerCase())} />
          </Field>
          <Field label="First administrator's name" htmlFor="o-an">
            <input id="o-an" className="field" value={adminName} maxLength={200} required onChange={(e) => setAdminName(e.target.value)} />
          </Field>
          <Field label="First administrator's email" htmlFor="o-ae">
            <input id="o-ae" type="email" className="field" value={adminEmail} required onChange={(e) => setAdminEmail(e.target.value)} />
          </Field>
          <div className="md:col-span-2">
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "Creating" : "Create organisation"}
            </button>
          </div>
        </form>
        <div className="mt-4" aria-live="polite">
          {message ? <Notice tone={message.tone}>{message.text}</Notice> : null}
        </div>
      </section>
    </div>
  );
}
