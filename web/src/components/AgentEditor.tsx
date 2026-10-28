"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { AdminAgentDetail, AgentOverrides, ProviderInfo, RunRecord } from "@/api/types";
import { call, messageOf } from "@/lib/client";
import { RunResult } from "./RunView";
import { Field, Notice, Pill, StatusPill } from "./ui";

type Tab = "basic" | "configuration" | "guardrails" | "runs";
const TABS: [Tab, string][] = [
  ["basic", "Basic"],
  ["configuration", "Configuration"],
  ["guardrails", "Guardrails"],
  ["runs", "Runs"],
];
const DEMO_SITES = ["demo-hotel", "demo-office", "demo-clinic"];

type Spec = {
  status: string;
  config: {
    model: { provider: string; name: string };
    temperature: number;
    max_iterations: number;
    timeout_seconds: number;
    mcp_servers: string[];
    allowed_tools: string[];
    max_output_tokens?: number | null;
  };
  guardrails: {
    blocklist: { enabled: boolean; terms: string[] };
    content_safety: { enabled: boolean };
    prompt_shield: { enabled: boolean };
    grounding_check: boolean;
  };
};

export function AgentEditor({
  detail,
  providers,
}: {
  detail: AdminAgentDetail;
  providers: ProviderInfo[];
}) {
  const router = useRouter();
  const spec = detail.spec as unknown as Spec;
  const saved = detail.settings as AgentOverrides;
  const [tab, setTab] = useState<Tab>("basic");
  const [status, setStatus] = useState(saved.status ?? spec.status);
  const [provider, setProvider] = useState(saved.provider ?? "");
  const [model, setModel] = useState(saved.model ?? "");
  const [temperature, setTemperature] = useState(String(saved.temperature ?? ""));
  const [iterations, setIterations] = useState(String(saved.max_iterations ?? ""));
  const [timeout, setTimeoutValue] = useState(String(saved.timeout_seconds ?? ""));
  const [blockOn, setBlockOn] = useState(saved.blocklist_enabled ?? spec.guardrails.blocklist.enabled);
  const [terms, setTerms] = useState((saved.blocklist_terms ?? spec.guardrails.blocklist.terms).join("\n"));
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const info = detail.summary;

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    const overrides: AgentOverrides = {
      status: status as AgentOverrides["status"],
      provider: (provider || null) as AgentOverrides["provider"],
      model: model.trim() || null,
      temperature: temperature === "" ? null : Number(temperature),
      max_iterations: iterations === "" ? null : Number(iterations),
      timeout_seconds: timeout === "" ? null : Number(timeout),
      blocklist_enabled: blockOn,
      blocklist_terms: terms
        .split("\n")
        .map((t) => t.trim())
        .filter(Boolean),
    };
    try {
      await call("PUT", `/admin/agents/${info.id}/settings`, { body: overrides });
      setMessage({ tone: "ok", text: "Settings saved. They apply to the next run." });
      router.refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setMessage(null);
    try {
      await call("DELETE", `/admin/agents/${info.id}/settings`);
      setMessage({ tone: "ok", text: "Back to the built-in settings." });
      router.refresh();
      window.location.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: messageOf(e) });
      setBusy(false);
    }
  }

  return (
    <div>
      <div role="tablist" aria-label="Agent sections" className="mb-6 flex gap-1 border-b-2 border-ink">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            id={`tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            type="button"
            onClick={() => setTab(id)}
            className={`px-4 py-2 font-semibold ${tab === id ? "bg-ink text-paper" : "hover:bg-teal-wash"}`}
          >
            {label}
          </button>
        ))}
      </div>

      <form onSubmit={save}>
        <div role="tabpanel" id="panel-basic" aria-labelledby="tab-basic" hidden={tab !== "basic"}>
          <dl className="mb-6 grid gap-x-8 gap-y-3 sm:grid-cols-2">
            <div>
              <dt className="text-sm text-muted">Name</dt>
              <dd className="font-semibold">{info.name}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">Id</dt>
              <dd>{info.id}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-sm text-muted">What it does</dt>
              <dd className="max-w-[70ch]">{info.description}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">Domain</dt>
              <dd>{info.domain}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">Capabilities</dt>
              <dd className="flex flex-wrap gap-1">
                {info.capabilities.map((c) => (
                  <Pill key={c}>{c}</Pill>
                ))}
              </dd>
            </div>
            <div>
              <dt className="text-sm text-muted">Prompt</dt>
              <dd>
                {detail.prompt.version}, fingerprint <code>{detail.prompt.sha256.slice(0, 12)}</code>
              </dd>
            </div>
            <div>
              <dt className="text-sm text-muted">Built-in status</dt>
              <dd>
                <StatusPill status={String(detail.built_in_spec.status)} />
              </dd>
            </div>
          </dl>
          <Field
            label="Status"
            htmlFor="status"
            hint="Customers can only run an agent that is active. A draft or disabled agent answers that it is not switched on."
          >
            <select id="status" className="field max-w-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="draft">Draft</option>
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
            </select>
          </Field>
          <details className="mb-4">
            <summary className="cursor-pointer font-semibold">Instructions the model receives (read only)</summary>
            <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap border border-line bg-surface p-3 text-sm">{detail.prompt.text}</pre>
          </details>
        </div>

        <div role="tabpanel" id="panel-configuration" aria-labelledby="tab-configuration" hidden={tab !== "configuration"}>
          <div className="grid gap-x-6 md:grid-cols-2">
            <Field label="Model provider" htmlFor="provider" hint="Customers cannot choose this. Leave on the built-in choice unless you have a reason.">
              <select id="provider" className="field" value={provider} onChange={(e) => setProvider(e.target.value)}>
                <option value="">Built-in ({spec.config.model.provider})</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                    {p.available ? "" : " (no key set)"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Model name" htmlFor="model" hint={`Built-in: ${spec.config.model.name}. Leave empty for the provider's default.`}>
              <input id="model" className="field" value={model} maxLength={100} onChange={(e) => setModel(e.target.value)} />
            </Field>
            <Field label="Temperature" htmlFor="temperature" hint={`0 to 2. Built-in: ${spec.config.temperature}.`}>
              <input id="temperature" className="field" inputMode="decimal" value={temperature} onChange={(e) => setTemperature(e.target.value)} />
            </Field>
            <Field label="Attempts allowed" htmlFor="iterations" hint={`1 to 5, including retries after a failed check. Built-in: ${spec.config.max_iterations}.`}>
              <input id="iterations" className="field" inputMode="numeric" value={iterations} onChange={(e) => setIterations(e.target.value)} />
            </Field>
            <Field label="Time limit in seconds" htmlFor="timeout" hint={`Built-in: ${spec.config.timeout_seconds}.`}>
              <input id="timeout" className="field" inputMode="decimal" value={timeout} onChange={(e) => setTimeoutValue(e.target.value)} />
            </Field>
          </div>
          <h3 className="mb-1 font-semibold">Tools it may use (set in code)</h3>
          <p className="mb-4 text-sm text-muted">
            {spec.config.allowed_tools.length ? spec.config.allowed_tools.join(", ") : "None"}
            {spec.config.mcp_servers.length ? ` through ${spec.config.mcp_servers.join(", ")}` : ""}
          </p>
          <h3 className="mb-2 font-semibold">Providers on this installation</h3>
          <ul className="mb-4 space-y-1 text-sm">
            {providers.map((p) => (
              <li key={p.id}>
                <strong>{p.label}</strong>: {p.available ? <Pill tone="ok">key found</Pill> : <Pill tone="warn">no key</Pill>}{" "}
                <span className="text-muted">{p.note}</span>
              </li>
            ))}
          </ul>
        </div>

        <div role="tabpanel" id="panel-guardrails" aria-labelledby="tab-guardrails" hidden={tab !== "guardrails"}>
          <p className="mb-4 max-w-[70ch] text-sm text-muted">
            The grounding check is always on: every figure an agent writes is compared with the calculated facts and
            the answer is rejected if one does not match.
          </p>
          <label className="mb-2 flex items-center gap-2 font-semibold">
            <input type="checkbox" checked={blockOn} onChange={(e) => setBlockOn(e.target.checked)} />
            Block questions that contain certain words
          </label>
          <Field label="Blocked words or phrases" htmlFor="terms" hint="One for each line, up to 50. Matching ignores upper and lower case.">
            <textarea id="terms" className="field max-w-md" value={terms} onChange={(e) => setTerms(e.target.value)} disabled={!blockOn} />
          </Field>
          {detail.guardrails_not_implemented.length > 0 ? (
            <Notice tone="warn">
              Not built yet, so nothing is checked for: {detail.guardrails_not_implemented.join(", ").replaceAll("_", " ")}.
            </Notice>
          ) : null}
        </div>

        <div className="mt-6 flex flex-wrap items-center gap-3" hidden={tab === "runs"}>
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? "Saving" : "Save settings"}
          </button>
          <button className="btn" type="button" disabled={busy} onClick={reset}>
            Reset to built-in settings
          </button>
          <div aria-live="polite">{message ? <Notice tone={message.tone}>{message.text}</Notice> : null}</div>
        </div>
      </form>

      <div role="tabpanel" id="panel-runs" aria-labelledby="tab-runs" hidden={tab !== "runs"}>
        <Playground agentId={info.id} inputKind={info.input_kind} examples={info.examples} />
      </div>
    </div>
  );
}

function Playground({
  agentId,
  inputKind,
  examples,
}: {
  agentId: string;
  inputKind: string;
  examples: { label: string; value: string }[];
}) {
  const [site, setSite] = useState("demo-clinic");
  const [question, setQuestion] = useState("");
  const [audience, setAudience] = useState("manager");
  const [anomaly, setAnomaly] = useState("A1");
  const [provider, setProvider] = useState("test");
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { site, provider };
      if (inputKind === "question") body.question = question || examples[0]?.value || "";
      if (inputKind === "audience") body.audience = audience;
      if (inputKind === "anomaly") body.anomaly_id = anomaly;
      const record = await call<RunRecord>("POST", `/admin/agents/${agentId}/playground`, { body });
      setRuns((r) => [record, ...r]);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <Notice>
        Try this agent on the invented demo sites before switching it on. Nothing here touches a customer&apos;s data, and
        platform staff cannot see customers&apos; runs. This list holds only the runs you start on this page.
      </Notice>
      <form onSubmit={run} className="mt-4 grid max-w-3xl gap-x-6 md:grid-cols-2">
        <Field label="Demo site" htmlFor="pg-site">
          <select id="pg-site" className="field" value={site} onChange={(e) => setSite(e.target.value)}>
            {DEMO_SITES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Field>
        <Field label="Model" htmlFor="pg-provider" hint="The test model gives scripted replies and needs no key.">
          <select id="pg-provider" className="field" value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="test">Built-in test model</option>
            <option value="gemini">Google Gemini</option>
            <option value="nebius">Nebius</option>
          </select>
        </Field>
        {inputKind === "question" ? (
          <div className="md:col-span-2">
            <Field label="Question" htmlFor="pg-q">
              <input id="pg-q" className="field" value={question} maxLength={500} onChange={(e) => setQuestion(e.target.value)} placeholder={examples[0]?.value} />
            </Field>
            <div className="-mt-2 mb-4 flex flex-wrap gap-2">
              {examples.map((x) => (
                <button type="button" key={x.value} className="btn btn-small" onClick={() => setQuestion(x.value)}>
                  {x.label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {inputKind === "audience" ? (
          <Field label="Audience" htmlFor="pg-aud">
            <select id="pg-aud" className="field" value={audience} onChange={(e) => setAudience(e.target.value)}>
              <option value="manager">Manager</option>
              <option value="technical">Technician</option>
            </select>
          </Field>
        ) : null}
        {inputKind === "anomaly" ? (
          <Field label="Event id" htmlFor="pg-a" hint="Such as A1. The demo clinic has events.">
            <input id="pg-a" className="field" value={anomaly} maxLength={8} onChange={(e) => setAnomaly(e.target.value)} />
          </Field>
        ) : null}
        <div className="md:col-span-2">
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? "Running" : "Run agent"}
          </button>
        </div>
      </form>
      <div className="mt-6 space-y-6" aria-live="polite">
        {error ? <Notice tone="bad">{error}</Notice> : null}
        {runs.map((r) => (
          <div key={r.run_id} className="border border-line bg-surface p-4">
            <RunResult run={r} />
          </div>
        ))}
      </div>
    </div>
  );
}
