import { NextResponse, type NextRequest } from "next/server";
import { entraSettings } from "@/lib/entra";
import {
  AUTH_COOKIE,
  AUTH_COOKIE_PATH,
  readTokenResponse,
  sameSecret,
  tokenRequestBody,
  tokenUrl,
  unpackAttempt,
} from "@/lib/entra-flow";
import { cookieOptions, SESSION_COOKIE } from "@/lib/session";

// Where Microsoft sends the browser back. The one-time code is swapped for an access token here,
// on the server, with the client secret. The browser only ever receives the httpOnly cookie.
export async function GET(request: NextRequest) {
  const settings = entraSettings();
  if (settings.kind !== "on") return back(request, "setup");
  const { config } = settings;
  const params = request.nextUrl.searchParams;

  const attempt = unpackAttempt(request.cookies.get(AUTH_COOKIE)?.value);
  const state = params.get("state") ?? "";
  if (attempt === null || state === "" || !sameSecret(state, attempt.state)) {
    return back(request, "expired");
  }
  // From here the attempt is used up, whatever happens next.
  if (params.has("error")) {
    return back(request, params.get("error") === "access_denied" ? "cancelled" : "failed");
  }
  const code = params.get("code") ?? "";
  if (code === "" || code.length > 4096) return back(request, "failed");

  let token: { token: string; seconds: number } | null = null;
  try {
    const answer = await fetch(tokenUrl(config), {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: tokenRequestBody(config, code, attempt.verifier),
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    if (answer.ok) token = readTokenResponse(await answer.json());
  } catch {
    token = null;
  }
  if (token === null) return back(request, "failed");

  const response = NextResponse.redirect(new URL("/", config.publicUrl), 303);
  response.cookies.set(SESSION_COOKIE, token.token, cookieOptions(token.seconds));
  response.cookies.set(AUTH_COOKIE, "", { ...cookieOptions(0), path: AUTH_COOKIE_PATH });
  response.headers.set("Cache-Control", "no-store");
  return response;
}

function back(request: NextRequest, problem: string) {
  const settings = entraSettings();
  const base = settings.kind === "on" ? settings.config.publicUrl : request.url;
  const response = NextResponse.redirect(new URL(`/login?error=${problem}`, base), 303);
  response.cookies.set(AUTH_COOKIE, "", { ...cookieOptions(0), path: AUTH_COOKIE_PATH });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
