import type { Usage } from "@/api/types";
import { Notice, PageHeader } from "@/components/ui";
import { UsageTable } from "@/components/UsageTable";
import { backend } from "@/lib/backend";
import { tokens } from "@/lib/format";
import { settle } from "@/lib/safe";

export const metadata = { title: "Usage" };

export default async function UsagePage() {
  const usage = await settle(backend<Usage>("/usage?days=30"));
  return (
    <>
      <PageHeader title="Usage" lead="How much the agents were used in the last 30 days." />
      {usage.error ? (
        <Notice tone="bad">{usage.error}</Notice>
      ) : usage.data ? (
        <>
          <p className="mb-6 text-lg">
            <strong>{tokens(usage.data.runs)}</strong> runs, <strong>{tokens(usage.data.failed)}</strong> failed,{" "}
            <strong>{tokens(usage.data.input_tokens)}</strong> tokens in and <strong>{tokens(usage.data.output_tokens)}</strong> out.
          </p>
          <UsageTable lines={usage.data.lines} />
        </>
      ) : null}
    </>
  );
}
