import type { RunRecord } from "@/api/types";
import { utcDateTime, tokens } from "@/lib/format";
import { Notice, Pill, StatusPill } from "./ui";

type Out = Record<string, unknown>;

function FactTags({ ids }: { ids: unknown }) {
  if (!Array.isArray(ids) || ids.length === 0) return null;
  return (
    <span className="ml-2 inline-flex flex-wrap gap-1 align-middle">
      {ids.map((id) => (
        <span key={String(id)} className="rounded border border-line px-1 text-xs text-muted" title="Fact in the evidence">
          {String(id)}
        </span>
      ))}
    </span>
  );
}

const text = (v: unknown) => (typeof v === "string" ? v : "");
const list = (v: unknown): Out[] => (Array.isArray(v) ? (v as Out[]) : []);

/** The checked answer of an agent, laid out by its shape. Every figure was checked against the
 * evidence in the backend before the answer was kept. */
export function AgentOutput({ output }: { output: Out }) {
  if ("hypotheses" in output) {
    return (
      <div>
        <p className="mb-3">{text(output.summary)}</p>
        <Notice tone="warn">
          These are possible causes to check, not findings. One electricity meter cannot show what
          caused a change.
        </Notice>
        <ol className="mt-4 space-y-4">
          {list(output.hypotheses).map((h, i) => (
            <li key={i} className="border-l-4 border-line pl-4">
              <p className="font-semibold">
                {text(h.statement)} <Pill tone={h.confidence === "medium" ? "warn" : "neutral"}>{text(h.confidence)} confidence</Pill>
                <FactTags ids={h.fact_ids} />
              </p>
              <p className="mt-1 text-sm text-muted">Why this label: {text(h.confidence_reason)}</p>
              <p className="mt-1 text-sm">
                <span className="font-semibold">Check:</span> {text(h.suggested_check)}
              </p>
            </li>
          ))}
        </ol>
      </div>
    );
  }
  if ("sections" in output) {
    return (
      <div>
        <p className="mb-4 text-xl font-semibold">{text(output.headline)}</p>
        {list(output.sections).map((s, i) => (
          <div key={i} className="mb-4">
            <h3 className="font-semibold">{text(s.heading)}</h3>
            <p className="max-w-[70ch]">
              {text(s.body)}
              <FactTags ids={s.fact_ids} />
            </p>
          </div>
        ))}
      </div>
    );
  }
  if ("actions" in output) {
    const actions = list(output.actions);
    return (
      <div>
        <p className="mb-3">{text(output.summary)}</p>
        {actions.length === 0 ? (
          <p className="text-muted">The data does not support any saving suggestion for this site.</p>
        ) : (
          <ul className="space-y-4">
            {actions.map((a, i) => (
              <li key={i} className="border-l-4 border-teal pl-4">
                <p className="font-semibold">{text(a.title)}</p>
                <p className="max-w-[70ch]">
                  {text(a.explanation)}
                  <FactTags ids={a.fact_ids} />
                </p>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-4 text-sm text-muted">{text(output.caveat)}</p>
      </div>
    );
  }
  if ("findings" in output) {
    return (
      <div>
        <p className="mb-3">{text(output.summary)}</p>
        <ul className="list-disc space-y-2 pl-5">
          {list(output.findings).map((f, i) => (
            <li key={i}>
              {text(f.text)}
              <FactTags ids={f.fact_ids} />
            </li>
          ))}
        </ul>
      </div>
    );
  }
  return <pre className="overflow-x-auto text-sm">{JSON.stringify(output, null, 2)}</pre>;
}

export function RunMeta({ run }: { run: RunRecord }) {
  const usage = run.usage ?? { input_tokens: 0, output_tokens: 0, estimated: false };
  const test = run.model_provider === "test-adapter";
  return (
    <div className="mt-4 space-y-2 border-t border-line pt-3 text-sm text-muted">
      {test ? (
        <Notice tone="warn">
          This answer came from the built-in test model, which produces scripted replies for trying
          the screens. It is not a real model&apos;s answer.
        </Notice>
      ) : null}
      <p>
        <StatusPill status={run.status} /> {run.agent}, model {run.model_name} ({run.model_provider}), prompt{" "}
        {run.prompt_version}, {run.attempts} {run.attempts === 1 ? "attempt" : "attempts"},{" "}
        {tokens(usage.input_tokens ?? 0)} in and {tokens(usage.output_tokens ?? 0)} out
        {usage.estimated ? " (estimated)" : ""} tokens, finished {utcDateTime(run.finished_at)}.
      </p>
    </div>
  );
}

export function RunResult({ run }: { run: RunRecord }) {
  return (
    <div>
      {run.status === "failed" ? (
        <Notice tone="bad">
          The agent could not produce an answer that passed its checks
          {run.error_kind ? ` (${run.error_kind.replaceAll("_", " ")})` : ""}. {run.error}
        </Notice>
      ) : run.output ? (
        <AgentOutput output={run.output as Out} />
      ) : null}
      <RunMeta run={run} />
    </div>
  );
}
