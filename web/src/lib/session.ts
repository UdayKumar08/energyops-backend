import "server-only";
import { cookies } from "next/headers";

export const SESSION_COOKIE = "eo_session";

/** The backend's address. Server-side only: it is never sent to the browser. */
export function backendUrl(): string {
  return (process.env.BACKEND_URL ?? "http://127.0.0.1:8100").replace(/\/+$/, "");
}

export async function readToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

export function cookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    // Plain http is allowed only for local development on this machine.
    secure: process.env.NODE_ENV === "production" && process.env.INSECURE_LOCAL_COOKIE !== "1",
    path: "/",
    maxAge: maxAgeSeconds,
  };
}
