import type { PlatformUsage } from "@/api/types";
import { Notice, PageHeader } from "@/components/ui";
import { UsageTable } from "@/components/UsageTable";
import { backend } from "@/lib/backend";
import { tokens } from "@/lib/format";
import { settle } from "@/lib/safe";

export const metadata = { title: "Platform usage" };

export default async function PlatformUsagePage() {
  const usage = await settle(backend<PlatformUsage>("/admin/usage?days=30"));
  return (
    <>
      <PageHeader title="Usage" lead="Runs and tokens for each organisation and agent over the last 30 days." />
      {usage.error ? (
        <Notice tone="bad">{usage.error}</Notice>
      ) : usage.data ? (
        <>
          <p className="mb-2 text-lg">
            <strong>{tokens(usage.data.runs)}</strong> runs, <strong>{tokens(usage.data.failed)}</strong> failed,{" "}
            <strong>{tokens(usage.data.input_tokens)}</strong> tokens in and <strong>{tokens(usage.data.output_tokens)}</strong> out.
          </p>
          <p className="mb-6 max-w-[70ch] text-sm text-muted">{usage.data.note}</p>
          <UsageTable lines={usage.data.lines} withOrganisation />
        </>
      ) : null}
    </>
  );
}
