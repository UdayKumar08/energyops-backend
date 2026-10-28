"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { OrgPerson, SiteListItem } from "@/api/types";
import { call, messageOf } from "@/lib/client";
import { Notice, Pill } from "./ui";

const ROLES = [
  ["viewer", "Viewer: sees data and runs"],
  ["technician", "Technician: also asks questions and drafts tickets"],
  ["manager", "Manager: also approves tickets, uploads data, sets tariffs, manages knowledge"],
  ["org_admin", "Organisation admin: also manages people, sites and the audit log"],
] as const;

function SitePicker({
  sites,
  value,
  onChange,
  idPrefix,
}: {
  sites: SiteListItem[];
  value: string[] | null;
  onChange: (next: string[] | null) => void;
  idPrefix: string;
}) {
  const all = value === null;
  return (
    <fieldset>
      <legend className="label">Sites they can see</legend>
      <label className="mb-1 flex items-center gap-2">
        <input type="checkbox" checked={all} onChange={(e) => onChange(e.target.checked ? null : [])} />
        All sites, including ones added later
      </label>
      {!all
        ? sites.map((s) => (
            <label key={s.slug} className="mb-1 ml-6 flex items-center gap-2" htmlFor={`${idPrefix}-${s.slug}`}>
              <input
                id={`${idPrefix}-${s.slug}`}
                type="checkbox"
                checked={value.includes(s.slug)}
                onChange={(e) =>
                  onChange(e.target.checked ? [...value, s.slug] : value.filter((x) => x !== s.slug))
                }
              />
              {s.name}
            </label>
          ))
        : null}
    </fieldset>
  );
}

function PersonRow({ person, sites, you }: { person: OrgPerson; sites: SiteListItem[]; you: boolean }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [role, setRole] = useState(person.role);
  const [picked, setPicked] = useState<string[] | null>(person.sites);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function patch(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await call("PATCH", `/users/${person.id}`, { body });
      setEditing(false);
      router.refresh();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <tr>
        <td>
          {person.name} {you ? <Pill>you</Pill> : null}
          <span className="block text-sm text-muted">{person.email}</span>
        </td>
        <td>{person.role.replaceAll("_", " ")}</td>
        <td>{person.sites === null ? "All sites" : person.sites.length === 0 ? "None" : person.sites.join(", ")}</td>
        <td>{person.is_active ? <Pill tone="ok">active</Pill> : <Pill tone="bad">deactivated</Pill>}</td>
        <td>
          <div className="flex gap-2">
            <button className="btn btn-small" type="button" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
              {editing ? "Close" : "Change"}
            </button>
            {!you ? (
              <button
                className={`btn btn-small ${person.is_active ? "btn-danger" : ""}`}
                type="button"
                disabled={busy}
                onClick={() => patch({ is_active: !person.is_active })}
              >
                {person.is_active ? "Deactivate" : "Reactivate"}
              </button>
            ) : null}
          </div>
        </td>
      </tr>
      {editing ? (
        <tr>
          <td colSpan={5} className="bg-surface">
            <div className="grid gap-6 p-2 md:grid-cols-2">
              <div>
                <label className="label" htmlFor={`role-${person.id}`}>
                  Role
                </label>
                <select id={`role-${person.id}`} className="field" value={role} disabled={you} onChange={(e) => setRole(e.target.value)}>
                  {ROLES.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                {you ? <p className="hint">You cannot change your own role.</p> : null}
              </div>
              <SitePicker sites={sites} value={picked} onChange={setPicked} idPrefix={`s-${person.id}`} />
            </div>
            <div className="flex items-center gap-3 p-2">
              <button
                className="btn btn-primary btn-small"
                type="button"
                disabled={busy}
                onClick={() =>
                  patch({
                    ...(you ? {} : { role }),
                    ...(picked === null ? { all_sites: true } : { sites: picked }),
                  })
                }
              >
                Save changes
              </button>
              {error ? <Notice tone="bad">{error}</Notice> : null}
            </div>
          </td>
        </tr>
      ) : error ? (
        <tr>
          <td colSpan={5}>
            <Notice tone="bad">{error}</Notice>
          </td>
        </tr>
      ) : null}
    </>
  );
}

export function PeopleManager({
  people,
  sites,
  meId,
}: {
  people: OrgPerson[];
  sites: SiteListItem[];
  meId: string;
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number][0]>("viewer");
  const [picked, setPicked] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await call("POST", "/users", { body: { email, name, role, sites: picked } });
      setMessage({ tone: "ok", text: `${name} was added. They sign in with their own account.` });
      setEmail("");
      setName("");
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
          <caption className="sr-only">People in your organisation</caption>
          <thead>
            <tr>
              <th scope="col">Person</th>
              <th scope="col">Role</th>
              <th scope="col">Sites</th>
              <th scope="col">Status</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {people.map((p) => (
              <PersonRow key={p.id} person={p} sites={sites} you={p.id === meId} />
            ))}
          </tbody>
        </table>
      </div>

      <section aria-labelledby="add-person">
        <h2 id="add-person" className="mb-3 text-xl font-semibold">
          Add a person
        </h2>
        <form onSubmit={add} className="grid max-w-3xl gap-x-6 md:grid-cols-2">
          <div className="mb-4">
            <label className="label" htmlFor="p-name">Name</label>
            <input id="p-name" className="field" value={name} maxLength={200} required onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="mb-4">
            <label className="label" htmlFor="p-email">Email address</label>
            <input id="p-email" type="email" className="field" value={email} required onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="mb-4">
            <label className="label" htmlFor="p-role">Role</label>
            <select id="p-role" className="field" value={role} onChange={(e) => setRole(e.target.value as typeof role)}>
              {ROLES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="mb-4">
            <SitePicker sites={sites} value={picked} onChange={setPicked} idPrefix="new" />
          </div>
          <div className="md:col-span-2">
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "Adding" : "Add person"}
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
