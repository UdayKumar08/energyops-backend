"use client";

import { useState } from "react";
import { callSite, messageOf } from "@/lib/client";

export function LoginForm() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await callSite("POST", "/api/session", { body: { email } });
      // A full page load on purpose: it starts from a clean state after the session changes.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/");
    } catch (e) {
      setError(messageOf(e));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate>
      <label className="label" htmlFor="email">
        Email address
      </label>
      <input
        id="email"
        name="email"
        type="email"
        autoComplete="username"
        className="field"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
      />
      <p className="hint">
        This build signs in with an email address only, for local development. Sign-in with your
        organisation&apos;s Microsoft account is not set up yet.
      </p>
      {error ? (
        <div className="notice notice-bad mt-4" role="alert">
          {error}
        </div>
      ) : null}
      <button className="btn btn-primary mt-4 w-full" type="submit" disabled={busy || email === ""}>
        {busy ? "Signing in" : "Sign in"}
      </button>
    </form>
  );
}
