import { NextResponse, type NextRequest } from "next/server";
import { entraSettings } from "@/lib/entra";
import { isSameOrigin } from "@/lib/proxy-path";
import { backendUrl, cookieOptions, SESSION_COOKIE } from "@/lib/session";

// Development sign-in and sign-out. Sign-in here is the backend's local development sign-in (an
// email, no password), which the backend itself only offers in its local environment. When
// Microsoft Entra is configured it is switched off here too; Entra sign-in lives under
// /api/auth/entra.

export async function POST(request: NextRequest) {
  if (!isSameOrigin(request.headers)) {
    return NextResponse.json({ detail: "That request did not come from this site." }, { status: 403 });
  }
  if (entraSettings().kind !== "off") {
    return NextResponse.json({ detail: "Sign in with Microsoft instead." }, { status: 403 });
  }
  let email = "";
  try {
    const body = (await request.json()) as { email?: unknown };
    if (typeof body.email === "string") email = body.email.trim();
  } catch {
    // falls through to the message below
  }
  if (email === "" || email.length > 320) {
    return NextResponse.json({ detail: "Enter your email address." }, { status: 422 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl() + "/auth/dev-login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "The service could not be reached." }, { status: 502 });
  }
  if (upstream.status === 404) {
    return NextResponse.json(
      { detail: "Development sign-in is not available here. Production sign-in is not set up yet." },
      { status: 501 },
    );
  }
  if (!upstream.ok) {
    const body = (await upstream.json().catch(() => ({}))) as { detail?: string };
    return NextResponse.json(
      { detail: body.detail ?? "That email address cannot sign in." },
      { status: upstream.status === 403 ? 403 : 502 },
    );
  }
  const { access_token, expires_in } = (await upstream.json()) as {
    access_token: string;
    expires_in: number;
  };
  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, access_token, cookieOptions(expires_in));
  return response;
}

export async function DELETE(request: NextRequest) {
  if (!isSameOrigin(request.headers)) {
    return NextResponse.json({ detail: "That request did not come from this site." }, { status: 403 });
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, "", cookieOptions(0));
  return response;
}
