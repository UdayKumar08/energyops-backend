"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { AskResponse, Forecast, SearchResult } from "@/api/types";
import { call, messageOf } from "@/lib/client";
import { canSpeak, createRecognition, speak, stopSpeaking, type Recognition } from "@/lib/speech";
import { ForecastDetail } from "./ForecastDetail";
import { Passages } from "./Passages";
import { RunResult } from "./RunView";
import { Notice, Pill, StatusPill } from "./ui";

type Turn = { question: string; answer?: AskResponse; error?: string };

function spoken(answer: AskResponse): string {
  const out = answer.run.output as { message?: string } | null;
  const child = answer.child?.output as { summary?: string; headline?: string } | null | undefined;
  return [out?.message, child?.summary ?? child?.headline].filter(Boolean).join(" ");
}

function Answer({ answer }: { answer: AskResponse }) {
  const out = (answer.run.output ?? {}) as {
    route?: string;
    outcome?: string;
    message?: string;
    result?: Record<string, unknown> | null;
    reason?: string;
  };
  if (answer.run.status === "failed") {
    return <Notice tone="bad">The assistant could not route this question. {answer.run.error}</Notice>;
  }
  return (
    <div className="space-y-3">
      <p>
        {out.outcome ? <StatusPill status={out.outcome} /> : null} <span className="ml-1">{out.message}</span>
      </p>
      {out.route === "forecast" && out.outcome === "answered" && out.result ? (
        <ForecastDetail forecast={out.result as unknown as Forecast} />
      ) : null}
      {out.route === "knowledge" && out.outcome === "answered" && out.result ? (
        <Passages result={out.result as unknown as SearchResult} />
      ) : null}
      {out.outcome === "ticket_pending" ? (
        <p>
          <Link className="underline" href="/tickets">
            Open the tickets page
          </Link>{" "}
          to approve or reject it.
        </p>
      ) : null}
      {answer.child ? <RunResult run={answer.child} /> : null}
      <p className="text-sm text-muted">
        Sent to <Pill>{out.route ?? "none"}</Pill>. {out.reason ? `Reason given: ${out.reason}` : ""}
      </p>
      {answer.run.model_provider === "test-adapter" ? (
        <Notice tone="warn">
          Routing here comes from the built-in test model, which matches keywords. It is not a real
          model&apos;s judgement.
        </Notice>
      ) : null}
    </div>
  );
}

export function Assistant({ site, siteName }: { site: string | null; siteName: string | null }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [voiceNote, setVoiceNote] = useState<string | null>(null);
  const [voice, setVoice] = useState({ hear: false, speak: false });
  const recognition = useRef<Recognition | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Which voice features this browser has can only be known in the browser.
    setVoice({ hear: createRecognition() !== null, speak: canSpeak() });
    return () => stopSpeaking();
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setQuestion("");
    setTurns((t) => [...t, { question: trimmed }]);
    try {
      const answer = await call<AskResponse>("POST", "/assistant/ask", { body: { question: trimmed, site } });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, answer } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: messageOf(e) } : x)));
    } finally {
      setBusy(false);
    }
  }

  function toggleListening() {
    if (listening) {
      recognition.current?.stop();
      return;
    }
    const r = createRecognition();
    if (!r) return;
    recognition.current = r;
    r.lang = "en-GB";
    r.interimResults = true;
    r.continuous = false;
    r.onresult = (e) => {
      const heard = Array.from(e.results)
        .map((x) => x[0].transcript)
        .join(" ");
      setQuestion(heard);
    };
    r.onerror = (e) => {
      setVoiceNote(
        e.error === "not-allowed"
          ? "The microphone is blocked. Allow it for this site in your browser, or type your question."
          : "Voice input stopped. You can type your question instead.",
      );
    };
    r.onend = () => setListening(false);
    setVoiceNote(null);
    setListening(true);
    r.start();
  }

  return (
    <div className="flex min-h-[60vh] flex-col">
      <p className="mb-4 text-sm text-muted">
        Asking about <strong className="text-ink">{siteName ?? "your first site"}</strong>. The assistant sends your
        question to the right part of the product. It cannot do anything your role does not allow.
      </p>

      <div className="flex-1 space-y-6" aria-live="polite">
        {turns.length === 0 ? (
          <div className="border border-dashed border-line p-6">
            <p className="font-semibold">Try asking</p>
            <ul className="mt-2 space-y-1">
              {[
                "What was the peak demand this period?",
                "How much could we save on our energy bill?",
                "What will demand be next week?",
                "Write a report for a manager.",
              ].map((q) => (
                <li key={q}>
                  <button type="button" className="underline underline-offset-2 hover:text-teal" onClick={() => ask(q)}>
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {turns.map((t, i) => (
          <div key={i} className="space-y-3">
            <p className="ml-auto max-w-[60ch] border-r-4 border-ink pr-3 text-right font-semibold">{t.question}</p>
            <div className="border border-line bg-surface p-4">
              {t.answer ? (
                <>
                  <Answer answer={t.answer} />
                  {voice.speak ? (
                    <div className="mt-3 flex gap-2">
                      <button type="button" className="btn btn-small" onClick={() => speak(spoken(t.answer!))}>
                        Read aloud
                      </button>
                      <button type="button" className="btn btn-small" onClick={stopSpeaking}>
                        Stop
                      </button>
                    </div>
                  ) : null}
                </>
              ) : t.error ? (
                <Notice tone="bad">{t.error}</Notice>
              ) : (
                <p className="text-muted">Working on it</p>
              )}
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>

      <form
        className="sticky bottom-0 mt-6 border-t-2 border-ink bg-paper pt-4"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
      >
        <label htmlFor="question" className="label">
          Your question
        </label>
        <div className="flex gap-2">
          <input
            id="question"
            className="field"
            value={question}
            maxLength={500}
            onChange={(e) => setQuestion(e.target.value)}
            autoComplete="off"
          />
          {voice.hear ? (
            <button type="button" className="btn" onClick={toggleListening} aria-pressed={listening}>
              {listening ? "Stop listening" : "Speak"}
            </button>
          ) : null}
          <button className="btn btn-primary" type="submit" disabled={busy || question.trim() === ""}>
            {busy ? "Asking" : "Ask"}
          </button>
        </div>
        {voice.hear ? (
          <p className="hint">
            Speaking uses your browser&apos;s own speech recognition, which may send the audio to the browser&apos;s
            provider. Typing never does.
          </p>
        ) : (
          <p className="hint">Voice input is not available in this browser. Type your question.</p>
        )}
        {voiceNote ? <p className="mt-2 text-sm text-brick">{voiceNote}</p> : null}
      </form>
    </div>
  );
}
