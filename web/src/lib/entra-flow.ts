/**
 * Signing in with Microsoft Entra ID: the pure parts (settings, the sign-in address, the PKCE
 * values, reading the answer). Nothing here touches the network or cookies, so it can be tested
 * on its own. The routes under /api/auth/entra do the I/O.
 *
 * The flow is the authorization code flow with PKCE, run by this server as a confidential client.
 * The browser is sent to Microsoft, comes back with a one-time code, and this server swaps the
 * code for an access token for the EnergyOps API. That token goes into the same httpOnly cookie
 * the development sign-in uses. The backend checks the token itself on every request.
 */
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

export const AUTH_COOKIE = "eo_auth";
export const AUTH_COOKIE_PATH = "/api/auth/entra";
export const CALLBACK_PATH = "/api/auth/entra/callback";
const DEFAULT_AUTHORITY = "https://login.microsoftonline.com";
const NAME = /^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$/;

export type EntraConfig = {
  tenantId: string;
  clientId: string;
  clientSecret: string;
  /** The web app's public address, with no trailing slash. The redirect address is built from it. */
  publicUrl: string;
  authority: string;
};

export type EntraSettings =
  | { kind: "off" }
  | { kind: "broken"; problem: string }
  | { kind: "on"; config: EntraConfig };

type Env = Record<string, string | undefined>;

function isLocalHost(host: string): boolean {
  return host === "localhost" || host === "127.0.0.1";
}

/** Read the settings. Entra is on when the tenant, client and secret are all set. */
export function readEntraSettings(env: Env): EntraSettings {
  const tenantId = env.ENTRA_TENANT_ID?.trim() ?? "";
  const clientId = env.ENTRA_CLIENT_ID?.trim() ?? "";
  const clientSecret = env.ENTRA_CLIENT_SECRET ?? "";
  if (tenantId === "" && clientId === "" && clientSecret === "") return { kind: "off" };

  const missing = [
    ["ENTRA_TENANT_ID", tenantId],
    ["ENTRA_CLIENT_ID", clientId],
    ["ENTRA_CLIENT_SECRET", clientSecret],
    ["PUBLIC_URL", env.PUBLIC_URL?.trim() ?? ""],
  ]
    .filter(([, value]) => value === "")
    .map(([name]) => name);
  if (missing.length > 0) {
    return { kind: "broken", problem: `Missing settings: ${missing.join(", ")}.` };
  }
  if (!NAME.test(tenantId) || !NAME.test(clientId)) {
    return { kind: "broken", problem: "ENTRA_TENANT_ID or ENTRA_CLIENT_ID is not valid." };
  }

  const localOnly = env.INSECURE_LOCAL_COOKIE === "1";
  let publicUrl: URL;
  try {
    publicUrl = new URL(env.PUBLIC_URL!.trim());
  } catch {
    return { kind: "broken", problem: "PUBLIC_URL is not a web address." };
  }
  const httpsOk = publicUrl.protocol === "https:";
  const localOk = localOnly && publicUrl.protocol === "http:" && isLocalHost(publicUrl.hostname);
  if (!httpsOk && !localOk) {
    return { kind: "broken", problem: "PUBLIC_URL must start with https://." };
  }

  // A different sign-in server is only for the browser tests, and only on a local machine.
  let authority = DEFAULT_AUTHORITY;
  const override = env.ENTRA_TEST_AUTHORITY?.trim();
  if (override) {
    if (!localOnly) {
      return { kind: "broken", problem: "ENTRA_TEST_AUTHORITY is only allowed for local testing." };
    }
    authority = override.replace(/\/+$/, "");
  }
  return {
    kind: "on",
    config: {
      tenantId,
      clientId,
      clientSecret,
      publicUrl: publicUrl.origin,
      authority,
    },
  };
}

export function redirectUri(config: EntraConfig): string {
  return config.publicUrl + CALLBACK_PATH;
}

/** The permission the API's tokens carry. The backend requires it. */
export function apiScope(config: EntraConfig): string {
  return `api://${config.clientId}/access_as_user`;
}

function base64Url(bytes: Buffer): string {
  return bytes.toString("base64url");
}

export type Pkce = { verifier: string; challenge: string };

export function newPkce(): Pkce {
  const verifier = base64Url(randomBytes(48));
  const challenge = base64Url(createHash("sha256").update(verifier).digest());
  return { verifier, challenge };
}

export function newState(): string {
  return base64Url(randomBytes(24));
}

export function authorizeUrl(config: EntraConfig, state: string, challenge: string): string {
  const url = new URL(`${config.authority}/${config.tenantId}/oauth2/v2.0/authorize`);
  url.search = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    redirect_uri: redirectUri(config),
    response_mode: "query",
    scope: apiScope(config),
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
    // Always let the person pick the account, so signing out and in again can change account.
    prompt: "select_account",
  }).toString();
  return url.toString();
}

export function tokenUrl(config: EntraConfig): string {
  return `${config.authority}/${config.tenantId}/oauth2/v2.0/token`;
}

export function tokenRequestBody(config: EntraConfig, code: string, verifier: string): string {
  return new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri(config),
    code_verifier: verifier,
    scope: apiScope(config),
  }).toString();
}

const MAX_SESSION_SECONDS = 24 * 60 * 60;

/** The access token and how long to keep it, or null if the answer is not what was asked for. */
export function readTokenResponse(body: unknown): { token: string; seconds: number } | null {
  if (typeof body !== "object" || body === null) return null;
  const { access_token, token_type, expires_in } = body as Record<string, unknown>;
  if (typeof access_token !== "string" || access_token.length < 10 || access_token.length > 8192) return null;
  if (typeof token_type !== "string" || token_type.toLowerCase() !== "bearer") return null;
  if (typeof expires_in !== "number" || !Number.isFinite(expires_in) || expires_in <= 0) return null;
  return { token: access_token, seconds: Math.min(Math.floor(expires_in), MAX_SESSION_SECONDS) };
}

/** The saved start of a sign-in, kept in a short-lived cookie until the person comes back. */
export function packAttempt(state: string, verifier: string): string {
  return Buffer.from(JSON.stringify({ s: state, v: verifier })).toString("base64url");
}

export function unpackAttempt(value: string | undefined): { state: string; verifier: string } | null {
  if (!value) return null;
  try {
    const data = JSON.parse(Buffer.from(value, "base64url").toString("utf8")) as { s?: unknown; v?: unknown };
    if (typeof data.s !== "string" || typeof data.v !== "string") return null;
    return { state: data.s, verifier: data.v };
  } catch {
    return null;
  }
}

/** Compare without leaking where the first difference is. */
export function sameSecret(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

/** The fixed words shown on the sign-in page for each way a sign-in can fail. */
export const SIGN_IN_PROBLEMS: Record<string, string> = {
  cancelled: "Sign-in was cancelled. Try again when you are ready.",
  failed: "Microsoft sign-in did not finish. Try again, and tell your administrator if it keeps happening.",
  expired: "That sign-in took too long or was already used. Start again.",
  setup: "Sign-in with Microsoft is not set up correctly on this server. Tell your administrator.",
  denied:
    "That Microsoft account is not set up in EnergyOps, or has been switched off. Ask your administrator to add you, or choose a different account.",
};
