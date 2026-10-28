"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { KnowledgeDocument, SearchResult } from "@/api/types";
import { call, messageOf } from "@/lib/client";
import { utcDateTime } from "@/lib/format";
import { Passages } from "./Passages";
import { Empty, Notice, Pill } from "./ui";

export function KnowledgeManager({
  documents,
  canManage,
}: {
  documents: KnowledgeDocument[];
  canManage: boolean;
}) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<SearchResult | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setMessage(null);
    try {
      const text = await file.text();
      const q = new URLSearchParams({ title: title.trim() || file.name, filename: file.name.slice(0, 200) });
      const doc = await call<KnowledgeDocument>("POST", `/knowledge/documents?${q}`, {
        raw: { text, contentType: "text/plain" },
      });
      setMessage({ tone: "ok", text: `Added "${doc.title}" as ${doc.passages} searchable passages.` });
      setFile(null);
      setTitle("");
      router.refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  }

  async function remove(doc: KnowledgeDocument) {
    setBusy(true);
    setMessage(null);
    try {
      await call("DELETE", `/knowledge/documents/${doc.id}`);
      setMessage({ tone: "ok", text: `Removed "${doc.title}".` });
      setFound(null);
      router.refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  }

  async function search(event: React.FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      setFound(await call<SearchResult>("GET", `/knowledge/search?${new URLSearchParams({ q: query.trim() })}`));
    } catch (e) {
      setMessage({ tone: "bad", text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-10">
      <section aria-labelledby="search-h">
        <h2 id="search-h" className="mb-3 text-xl font-semibold">
          Search your documents
        </h2>
        <form onSubmit={search} className="flex max-w-xl gap-2">
          <label htmlFor="q" className="sr-only">
            What are you looking for
          </label>
          <input id="q" className="field" value={query} maxLength={500} onChange={(e) => setQuery(e.target.value)} placeholder="For example: boiler lockout" />
          <button className="btn btn-primary" type="submit" disabled={busy || !query.trim()}>
            Search
          </button>
        </form>
        <div className="mt-4" aria-live="polite">
          {found ? <Passages result={found} /> : null}
        </div>
      </section>

      <section aria-labelledby="docs-h">
        <h2 id="docs-h" className="mb-3 text-xl font-semibold">
          Documents
        </h2>
        {documents.length === 0 ? (
          <Empty title="No documents yet">
            {canManage ? "Upload a procedure, manual or policy as a text or markdown file." : "A manager can upload documents here."}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Title</th>
                  <th scope="col" className="num">Passages</th>
                  <th scope="col">Added (UTC)</th>
                  <th scope="col">Search</th>
                  {canManage ? <th scope="col"><span className="sr-only">Actions</span></th> : null}
                </tr>
              </thead>
              <tbody>
                {documents.map((d) => (
                  <tr key={d.id}>
                    <td>
                      {d.title}
                      {d.filename ? <span className="block text-sm text-muted">{d.filename}</span> : null}
                    </td>
                    <td className="num">{d.passages}</td>
                    <td>{utcDateTime(d.uploaded_at)}</td>
                    <td>{d.searchable ? <Pill tone="ok">searchable</Pill> : <Pill tone="warn">upload again</Pill>}</td>
                    {canManage ? (
                      <td>
                        <button className="btn btn-small btn-danger" type="button" disabled={busy} onClick={() => remove(d)}>
                          Remove
                        </button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {canManage ? (
        <section aria-labelledby="add-h">
          <h2 id="add-h" className="mb-3 text-xl font-semibold">
            Add a document
          </h2>
          <form onSubmit={upload} className="max-w-md">
            <label htmlFor="doc-file" className="label">
              Text or markdown file
            </label>
            <input
              id="doc-file"
              type="file"
              accept=".txt,.md,text/plain,text/markdown"
              className="field"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setFile(f);
                if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, ""));
              }}
            />
            <p className="hint">Up to 1 MB. PDF and Word files are not supported; save them as text first.</p>
            <label htmlFor="doc-title" className="label mt-4">
              Title
            </label>
            <input id="doc-title" className="field" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
            <button className="btn btn-primary mt-4" type="submit" disabled={!file || busy}>
              {busy ? "Adding" : "Add document"}
            </button>
          </form>
        </section>
      ) : null}

      <div aria-live="polite">{message ? <Notice tone={message.tone}>{message.text}</Notice> : null}</div>
    </div>
  );
}
