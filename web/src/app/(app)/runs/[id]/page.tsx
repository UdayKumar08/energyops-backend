import Link from "next/link";
import { notFound } from "next/navigation";
import type { RunRecord } from "@/api/types";
import { RunResult } from "@/components/RunView";
import { Empty, PageHeader, Section } from "@/components/ui";
import { backendOrNull } from "@/lib/backend";
import { utcDateTime } from "@/lib/format";
import { agentName, stepTime, took } from "@/lib/runs";

export const metadata = { title: "Run" };

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const run = await backendOrNull<RunRecord>(`/runs/${encodeURIComponent(id)}`);
  if (run === null) notFound();
  const question = typeof run.input?.question === "string" ? run.input.question : null;
  const steps = run.steps ?? [];

  return (
    <>
      <PageHeader
        title={agentName(run.agent)}
        lead={`Started ${utcDateTime(run.started_at)} and took ${took(run.started_at, run.finished_at)}.`}
        actions={
          <Link className="btn" href="/runs">
            Back to run history
          </Link>
        }
      />
      {question ? (
        <Section title="Question">
          <p className="max-w-[70ch]">{question}</p>
        </Section>
      ) : null}
      <Section title="Answer">
        <RunResult run={run} />
      </Section>
      <Section title="Steps" note="What the agent did, in order, and how long each step took.">
        {steps.length === 0 ? (
          <Empty title="No steps were recorded for this run" />
        ) : (
          <ol className="max-w-[70ch] divide-y divide-line border-y border-line">
            {steps.map((s, i) => (
              <li key={i} className="flex justify-between gap-4 py-2">
                <span>{s.name.replaceAll("_", " ")}</span>
                <span className="whitespace-nowrap text-muted">{stepTime(s.duration_ms)}</span>
              </li>
            ))}
          </ol>
        )}
      </Section>
    </>
  );
}
