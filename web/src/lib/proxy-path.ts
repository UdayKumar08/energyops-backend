/**
 * Which API paths the browser may reach through the web server, and where they go.
 *
 * The browser never talks to the backend. It calls /api/<path> on this web server, which adds the
 * signed-in person's token from an httpOnly cookie and forwards to the backend. Sign-in has its
 * own route (/api/session), so the backend's /auth/* is not reachable through this one, and
 * neither is anything that is not a plain path segment.
 */

const SEGMENT = /^[A-Za-z0-9._~-]+$/;
const BLOCKED_FIRST = new Set(["auth", "health", "docs", "openapi.json", "redoc"]);

export function backendPathFor(segments: readonly string[]): string | null {
  if (segments.length === 0) return null;
  for (const segment of segments) {
    if (!SEGMENT.test(segment) || segment === "." || segment === "..") return null;
  }
  if (BLOCKED_FIRST.has(segments[0] ?? "")) return null;
  return "/" + segments.join("/");
}

/**
 * True when a state-changing request came from this site's own pages. Browsers send Origin on
 * every cross-site POST/PUT/PATCH/DELETE, so a mismatch (or a missing Origin together with a
 * Sec-Fetch-Site that is not same-origin) is refused. With the SameSite=Lax cookie this stops
 * another site from acting as the signed-in person.
 */
export function isSameOrigin(headers: { get(name: string): string | null }): boolean {
  const host = headers.get("host");
  const origin = headers.get("origin");
  if (origin !== null) {
    try {
      return host !== null && new URL(origin).host === host;
    } catch {
      return false;
    }
  }
  const site = headers.get("sec-fetch-site");
  return site === null || site === "same-origin" || site === "none";
}

export const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
