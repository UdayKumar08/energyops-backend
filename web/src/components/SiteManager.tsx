"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { call, messageOf } from "@/lib/client";
import { Notice } from "./ui";

export function SiteManager() {
  const router = useRouter();
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [interval, setInterval] = useState(15);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await call("POST", "/sites", { body: { slug, name, interval_minutes: interval } });
      setMessage({ tone: "ok", text: `${name} was added. Upload its readings to start.` });
      setSlug("");
      setName("");
      router.refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={add} className="max-w-xl">
      <div className="mb-4">
        <label className="label" htmlFor="s-name">Name</label>
        <input id="s-name" className="field" value={name} maxLength={200} required onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="mb-4">
        <label className="label" htmlFor="s-slug">Short name</label>
        <input id="s-slug" className="field" value={slug} maxLength={64} required pattern="[a-z0-9]+(-[a-z0-9]+)*" onChange={(e) => setSlug(e.target.value.toLowerCase())} />
        <p className="hint">Lower case letters, numbers and hyphens, such as north-wing. It cannot be changed later.</p>
      </div>
      <div className="mb-4">
        <label className="label" htmlFor="s-int">Minutes between meter readings</label>
        <select id="s-int" className="field" value={interval} onChange={(e) => setInterval(Number(e.target.value))}>
          {[5, 10, 15, 30, 60].map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>
      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? "Adding" : "Add site"}
      </button>
      <div className="mt-4" aria-live="polite">
        {message ? <Notice tone={message.tone}>{message.text}</Notice> : null}
      </div>
    </form>
  );
}
