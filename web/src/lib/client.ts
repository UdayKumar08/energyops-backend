"use client";

/** Calls from the browser. They go to this site's /api, which adds the sign-in. */

export class CallError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message);
  }
}

type Options = { body?: unknown; raw?: { text: string; contentType: string } };

/** A call to the backend through this site's proxy (/api/<path>). */
export function call<T>(method: string, path: string, options: Options = {}): Promise<T> {
  return send<T>(method, `/api${path}`, options);
}

/** A call to one of this site's own routes, such as /api/session. */
export function callSite<T>(method: string, fullPath: string, options: Options = {}): Promise<T> {
  return send<T>(method, fullPath, options);
}

async function send<T>(method: string, url: string, options: Options): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (options.raw) {
    headers["content-type"] = options.raw.contentType;
    body = options.raw.text;
  } else if (options.body !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(url, { method, headers, body });
  } catch {
    throw new CallError(0, "The service could not be reached. Check your connection and try again.");
  }
  if (response.status === 401) {
    // A full page load on purpose: it starts from a clean state after the session changes.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login?expired=1");
    throw new CallError(401, "Your sign-in has ended.");
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let parsed: unknown = undefined;
  try {
    parsed = text ? JSON.parse(text) : undefined;
  } catch {
    // leave parsed undefined
  }
  if (!response.ok) {
    const detail = (parsed as { detail?: unknown } | undefined)?.detail;
    throw new CallError(
      response.status,
      typeof detail === "string" ? detail : `The server answered ${response.status}.`,
      parsed,
    );
  }
  return parsed as T;
}

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Try again.";
}
