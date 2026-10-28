"use strict";
// Dev-only playground. Everything that came from the server or a model is inserted as text,
// never as HTML, and nothing here talks to any host except the one that served this page.

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) node.append(kid);
  return node;
}

function clear(node) {
  node.replaceChildren();
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const detail = body && body.detail !== undefined ? body.detail : response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

const state = { agentId: null, detail: null, agents: [], providers: [], record: null };

// ---------------------------------------------------------------- small views

function pill(text, kind = "") {
  return el("span", { class: `pill ${kind}`.trim(), text });
}

function definitions(pairs) {
  const list = el("dl");
  for (const [term, value] of pairs) {
    list.append(el("dt", { text: term }), el("dd", {}, value));
  }
  return list;
}

function chips(values) {
  if (!values || values.length === 0) return el("span", { class: "muted", text: "none" });
  return values.map((value) => el("span", { class: "chip", text: value }));
}

function dataPath(record) {
  const step = record.steps.find((s) => s.name === "fact_sheet");
  return step && step.data.via ? step.data.via : "unknown";
}

function seconds(record) {
  return (new Date(record.finished_at) - new Date(record.started_at)) / 1000;
}

// ---------------------------------------------------------------- agent tabs

function renderAgent() {
  const { summary, spec, prompt, guardrails_not_implemented: missing } = state.detail;
  $("agent-title").textContent = summary.name;
  $("agent-sub").textContent =
    `${summary.id} · declared model: ${summary.model_provider} / ${summary.model_name}`;
  const status = $("agent-status");
  status.textContent = summary.status;
  status.className = `pill ${summary.status === "active" ? "ok" : "warn"}`;

  const basic = $("panel-basic");
  clear(basic);
  basic.append(
    definitions([
      ["Name", summary.name],
      ["Domain", summary.domain],
      ["Status", summary.status],
      ["Description", summary.description],
      ["Capabilities", chips(summary.capabilities)],
    ]),
    el("p", {
      class: "muted small",
      text: "Read only. These fields come from the agent's specification in code.",
    }),
  );

  const config = spec.config;
  const configPanel = $("panel-config");
  clear(configPanel);
  configPanel.append(
    definitions([
      ["Declared provider", config.model.provider],
      ["Declared model", config.model.name],
      ["Temperature", String(config.temperature)],
      ["Max iterations", `${config.max_iterations} (model attempts, first try included)`],
      ["Timeout", `${config.timeout_seconds} seconds`],
      ["Max output tokens", String(config.max_output_tokens)],
      ["MCP servers", chips(config.mcp_servers)],
      ["Allowed tools", chips(config.allowed_tools)],
      ["Prompt version", prompt.version],
      ["Prompt fingerprint", prompt.sha256.slice(0, 16)],
    ]),
    el("p", {
      class: "muted small",
      text: "The declared model is the specification's default. Each run uses the model chosen in the playground.",
    }),
    el("h3", { text: "System prompt" }),
    el("pre", { text: prompt.text }),
  );

  const guard = spec.guardrails;
  const guardPanel = $("panel-guardrails");
  clear(guardPanel);
  const row = (name, enabled, note) =>
    el("div", { class: "finding" }, el("strong", { text: `${name}: ` }), pill(enabled ? "on" : "off"), el("p", { class: "muted small", text: note }));
  guardPanel.append(
    row("Content Safety", guard.content_safety.enabled, "Not implemented yet. A specification that switches it on refuses to start."),
    row(
      "Blocklist",
      guard.blocklist.enabled,
      guard.blocklist.terms.length ? `Terms: ${guard.blocklist.terms.join(", ")}` : "Implemented. No terms set.",
    ),
    row("Prompt Shield", guard.prompt_shield.enabled, "Not implemented yet. A specification that switches it on refuses to start."),
    row("Grounding check", true, "Always on. Every kWh, kW, % and day figure is checked against the computed fact sheet."),
  );
  if (missing.length) {
    guardPanel.prepend(el("p", { class: "banner", text: `Switched on but not implemented: ${missing.join(", ")}` }));
  }
}

async function renderRuns() {
  const panel = $("panel-runs");
  clear(panel);
  let runs;
  try {
    runs = await api(`/runs?agent=${encodeURIComponent(state.detail.spec.name)}&limit=50`);
  } catch (error) {
    panel.append(el("p", { class: "banner", text: error.message }));
    return;
  }
  if (runs.length === 0) {
    panel.append(el("p", { class: "muted", text: "No runs yet. Runs are kept in memory until the server stops." }));
    return;
  }
  const table = el("table");
  table.append(el("tr", {}, ...["Time", "Result", "Input", "Model", "Tries"].map((h) => el("th", { text: h }))));
  for (const run of runs) {
    const tr = el(
      "tr",
      { class: "click", tabindex: "0" },
      el("td", { text: new Date(run.started_at).toLocaleTimeString() }),
      el("td", {}, pill(run.status === "succeeded" ? "passed" : `failed: ${run.error_kind}`, run.status === "succeeded" ? "ok" : "bad")),
      el("td", { text: run.question }),
      el("td", { text: `${run.model_provider} / ${run.model_name}` }),
      el("td", { text: String(run.attempts) }),
    );
    const open = async () => showRecord(await api(`/runs/${encodeURIComponent(run.run_id)}`));
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter") open();
    });
    table.append(tr);
  }
  panel.append(el("div", { class: "scroll" }, table));
}

function selectTab(name) {
  for (const button of document.querySelectorAll(".tabs button")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === name));
  }
  for (const panel of document.querySelectorAll(".panel")) panel.hidden = panel.id !== `panel-${name}`;
  if (name === "runs") renderRuns();
}

// ---------------------------------------------------------------- response and trace

function showRecord(record) {
  state.record = record;
  $("response").hidden = false;
  const banner = $("banner");
  banner.hidden = record.model_provider !== "test-adapter";
  banner.textContent = "This answer came from the test adapter, a canned stand-in. It is not a real model.";

  const answer = $("answer");
  clear(answer);
  if (record.status === "succeeded" && record.output.sections) {
    answer.append(pill("passed the figure and structure check", "ok"), el("h4", { text: record.output.headline }));
    for (const section of record.output.sections) {
      answer.append(
        el(
          "div",
          { class: "finding" },
          el("strong", { text: section.heading }),
          el("p", { text: section.body }),
          section.fact_ids.length ? chips(section.fact_ids) : null,
        ),
      );
    }
  } else if (record.status === "succeeded" && record.output.hypotheses) {
    answer.append(
      pill("passed the figure and wording check", "ok"),
      el("p", { class: "banner", text: "These are hypotheses from one meter's readings, not findings. A person has to check them." }),
      el("p", { text: record.output.summary }),
    );
    for (const item of record.output.hypotheses) {
      answer.append(
        el(
          "div",
          { class: "finding" },
          el("div", {}, pill(`confidence: ${item.confidence}`, item.confidence === "medium" ? "warn" : ""), " ", el("span", { text: item.statement })),
          el("p", { class: "muted small", text: `Suggested check: ${item.suggested_check}` }),
          chips(item.fact_ids),
        ),
      );
    }
  } else if (record.status === "succeeded") {
    answer.append(pill("passed the figure check", "ok"), el("p", { text: record.output.summary }));
    for (const finding of record.output.findings) {
      answer.append(el("div", { class: "finding" }, el("div", { text: finding.text }), chips(finding.fact_ids)));
    }
  } else {
    answer.append(
      pill(`failed: ${record.error_kind}`, "bad"),
      el("p", { text: record.error || "" }),
      el("p", { class: "muted small", text: "The last reply and the reasons are in the trace." }),
    );
  }

  const usage = record.usage;
  $("meta").textContent =
    `ran on ${record.model_provider} / ${record.model_name} · ${record.attempts} attempt(s) · ` +
    `${usage.input_tokens} in / ${usage.output_tokens} out tokens${usage.estimated ? " (estimated)" : ""} · ` +
    `${seconds(record).toFixed(1)} s · prompt ${record.prompt_version} · data via ${dataPath(record)} · run ${record.run_id.slice(0, 8)}`;

  const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" });
  if ($("download").dataset.url) URL.revokeObjectURL($("download").dataset.url);
  const url = URL.createObjectURL(blob);
  $("download").dataset.url = url;
  $("download").href = url;
  $("download").download = `run-${record.run_id.slice(0, 8)}.json`;

  renderTrace(record);
  setTrace(false);
}

function renderTrace(record) {
  const trace = $("trace");
  clear(trace);
  for (const step of record.steps) {
    const attempt = step.data.attempt !== undefined ? ` (attempt ${step.data.attempt})` : "";
    trace.append(
      el(
        "details",
        {},
        el("summary", {}, el("strong", { text: step.name + attempt }), el("span", { class: "step-time", text: `${step.duration_ms} ms` })),
        el("pre", { text: JSON.stringify(step.data, null, 2) }),
      ),
    );
  }
}

function setTrace(open) {
  $("trace").hidden = !open;
  $("toggle-trace").setAttribute("aria-expanded", String(open));
  $("toggle-trace").textContent = open ? "Hide trace" : "View trace";
}

// ---------------------------------------------------------------- running

function renderProviders() {
  const select = $("provider");
  clear(select);
  for (const provider of state.providers) {
    const option = el("option", { value: provider.id, text: provider.available ? provider.label : `${provider.label} (unavailable)` });
    if (!provider.available) option.disabled = true;
    select.append(option);
  }
  select.value = "test";
  updateProviderNote();
}

function updateProviderNote() {
  const provider = state.providers.find((p) => p.id === $("provider").value);
  if (!provider) return;
  $("model").placeholder = provider.default_model;
  $("provider-note").textContent =
    provider.id === "test" ? provider.note : "Real call. It uses a little of your free quota.";
}

function updateCount() {
  $("count").textContent = `${$("question").value.length} / 500`;
}

async function runAgent() {
  const kind = state.detail.summary.input_kind;
  const question = $("question").value.trim();
  const status = $("run-status");
  if (kind === "question" && !question) {
    status.textContent = "Type a question first.";
    return;
  }
  if (kind === "anomaly" && !$("event").value) {
    status.textContent = "Pick a site that has an event first.";
    return;
  }
  const body = { provider: $("provider").value, data_access: $("access").value };
  if (kind === "question") body.question = question;
  else if (kind === "audience") body.audience = $("audience").value;
  else body.anomaly_id = $("event").value;
  if ($("model").value.trim()) body.model = $("model").value.trim();
  if ($("site").value.trim()) body.site = $("site").value.trim();

  $("run").disabled = true;
  status.textContent = body.provider === "test" ? "Running..." : "Running. Real models can take 10 to 15 seconds...";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90000);
  try {
    const record = await api(`/agents/${encodeURIComponent(state.agentId)}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    showRecord(record);
    status.textContent = "Done.";
    if (!$("panel-runs").hidden) renderRuns();
  } catch (error) {
    status.textContent = error.name === "AbortError" ? "Stopped waiting after 90 seconds." : `Could not run: ${error.message}`;
  } finally {
    clearTimeout(timer);
    $("run").disabled = false;
  }
}

// ---------------------------------------------------------------- start

function applyInputKind() {
  const { input_kind: kind, examples } = state.detail.summary;
  $("question-row").hidden = kind !== "question";
  $("audience-row").hidden = kind !== "audience";
  $("event-row").hidden = kind !== "anomaly";
  const box = $("examples");
  clear(box);
  const audience = $("audience");
  clear(audience);
  if (kind === "question") {
    for (const example of examples) {
      const button = el("button", { type: "button", text: example.label });
      button.addEventListener("click", () => {
        $("question").value = example.value;
        updateCount();
      });
      box.append(button);
    }
    $("question").value = examples.length ? examples[0].value : "";
    updateCount();
  } else if (kind === "audience") {
    for (const example of examples) audience.append(el("option", { value: example.value, text: example.label }));
  } else {
    // The demo-hotel has no events, so start from the site that has some.
    if (!$("site").value.trim()) $("site").value = "demo-clinic";
    loadEvents();
  }
}

async function loadEvents() {
  const select = $("event");
  const note = $("event-note");
  clear(select);
  note.textContent = "Looking for events...";
  const site = $("site").value.trim();
  try {
    const found = await api(`/anomalies${site ? `?site=${encodeURIComponent(site)}` : ""}`);
    for (const event of found.anomalies) {
      const start = new Date(event.start).toISOString().replace("T", " ").slice(0, 16);
      select.append(el("option", { value: event.id, text: `${event.id}: ${event.kind}, ${start} UTC, ${(new Date(event.end) - new Date(event.start)) / 60000} minutes` }));
    }
    note.textContent = found.anomalies.length
      ? `${found.anomalies.length} event(s) found by the detector for ${found.meter_id}. They are unusual readings, not faults.`
      : `The detector found no events for ${found.meter_id}. Try demo-clinic.`;
  } catch (error) {
    note.textContent = `Could not list events: ${error.message}`;
  }
}

async function loadAgent(agentId) {
  state.agentId = agentId;
  state.detail = await api(`/agents/${encodeURIComponent(agentId)}`);
  renderAgent();
  applyInputKind();
  $("response").hidden = true;
  $("run-status").textContent = "";
  selectTab("basic");
}

async function start() {
  $("question").addEventListener("input", updateCount);
  $("question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) runAgent();
  });
  $("provider").addEventListener("change", updateProviderNote);
  $("site").addEventListener("change", () => {
    if (state.detail && state.detail.summary.input_kind === "anomaly") loadEvents();
  });
  $("run").addEventListener("click", runAgent);
  $("toggle-trace").addEventListener("click", () => setTrace($("trace").hidden));
  for (const button of document.querySelectorAll(".tabs button")) {
    button.addEventListener("click", () => selectTab(button.dataset.tab));
  }
  $("agent-picker").addEventListener("change", async () => {
    try {
      await loadAgent($("agent-picker").value);
    } catch (error) {
      $("run-status").textContent = `Could not load the agent: ${error.message}`;
    }
  });

  try {
    const [health, providers, agents] = await Promise.all([api("/health"), api("/providers"), api("/agents")]);
    $("local-tag").textContent = `${health.environment} environment, no sign-in`;
    state.providers = providers;
    renderProviders();
    if (agents.length === 0) throw new Error("The server lists no agents.");
    state.agents = agents;
    for (const agent of agents) $("agent-picker").append(el("option", { value: agent.id, text: agent.name }));
    await loadAgent(agents[0].id);
  } catch (error) {
    const box = $("load-error");
    box.hidden = false;
    box.textContent = `Could not load the playground: ${error.message}`;
  }
}

start();
