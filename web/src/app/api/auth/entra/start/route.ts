import { NextResponse, type NextRequest } from "next/server";
import { entraSettings } from "@/lib/entra";
import {
  AUTH_COOKIE,
  AUTH_COOKIE_PATH,
  authorizeUrl,
  newPkce,
  newState,
  packAttempt,
} from "@/lib/entra-flow";
import { cookieOptions } from "@/lib/session";

// Sends the browser to Microsoft's sign-in page. A random state and a PKCE verifier are kept in a
// short-lived httpOnly cookie, and checked when the person comes back.
export async function GET(request: NextRequest) {
  const settings = entraSettings();
  if (settings.kind !== "on") {
    return NextResponse.redirect(new URL("/login?error=setup", request.url), 303);
  }
  const { verifier, challenge } = newPkce();
  const state = newState();
  const response = NextResponse.redirect(authorizeUrl(settings.config, state, challenge), 303);
  response.cookies.set(AUTH_COOKIE, packAttempt(state, verifier), {
    ...cookieOptions(600),
    path: AUTH_COOKIE_PATH,
  });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
