import type { SearchResult } from "@/api/types";
import { Notice } from "./ui";

export function Passages({ result }: { result: Pick<SearchResult, "results" | "note" | "unsearchable_documents"> }) {
  return (
    <div className="space-y-4">
      {result.note ? <Notice tone="warn">{result.note}</Notice> : null}
      {result.unsearchable_documents > 0 ? (
        <Notice tone="warn">
          {result.unsearchable_documents} {result.unsearchable_documents === 1 ? "document was" : "documents were"} added
          with a different search method and cannot be searched. Upload {result.unsearchable_documents === 1 ? "it" : "them"} again.
        </Notice>
      ) : null}
      {result.results.length === 0 ? (
        <p className="text-muted">No passage in your documents matches. Try other words from the document.</p>
      ) : (
        <ul className="space-y-4">
          {result.results.map((r) => (
            <li key={`${r.document_id}-${r.position}`} className="border-l-4 border-teal pl-4">
              <p className="text-sm text-muted">
                {r.title}, passage {r.position + 1}, match {Math.round(r.score * 100)}%
              </p>
              <p className="max-w-[70ch] whitespace-pre-wrap">{r.text}</p>
            </li>
          ))}
        </ul>
      )}
      <p className="text-sm text-muted">These are the passages exactly as they were uploaded. Nothing has been summarised.</p>
    </div>
  );
}
