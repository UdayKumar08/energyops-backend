import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import {
  authorizeUrl,
  newPkce,
  newState,
  packAttempt,
  readEntraSettings,
  readTokenResponse,
  redirectUri,
  sameSecret,
  tokenRequestBody,
  unpackAttempt,
  type EntraConfig,
} from "@/lib/entra-flow";

const TENANT = "11111111-2222-3333-4444-555555555555";
const CLIENT = "99999999-8888-7777-6666-555555555555";
const good = {
  ENTRA_TENANT_ID: TENANT,
  ENTRA_CLIENT_ID: CLIENT,
  ENTRA_CLIENT_SECRET: "a-secret",
  PUBLIC_URL: "https://energyops.example.com/",
};

function on(env: Record<string, string | undefined>): EntraConfig {
  const settings = readEntraSettings(env);
  if (settings.kind !== "on") throw new Error(`expected on, got ${JSON.stringify(settings)}`);
  return settings.config;
}

describe("settings", () => {
  it("is off when nothing is set", () => {
    expect(readEntraSettings({})).toEqual({ kind: "off" });
  });

  it("is on when everything is set, and drops the trailing slash", () => {
    const config = on(good);
    expect(config.publicUrl).toBe("https://energyops.example.com");
    expect(config.authority).toBe("https://login.microsoftonline.com");
    expect(redirectUri(config)).toBe("https://energyops.example.com/api/auth/entra/callback");
  });

  it("is broken, naming what is missing, when only some settings are set", () => {
    const settings = readEntraSettings({ ENTRA_TENANT_ID: TENANT });
    expect(settings.kind).toBe("broken");
    expect(JSON.stringify(settings)).toContain("ENTRA_CLIENT_SECRET");
    expect(JSON.stringify(settings)).toContain("PUBLIC_URL");
  });

  it("refuses an address that is not https, except localhost on a local machine", () => {
    expect(readEntraSettings({ ...good, PUBLIC_URL: "http://energyops.example.com" }).kind).toBe("broken");
    expect(readEntraSettings({ ...good, PUBLIC_URL: "http://localhost:3000" }).kind).toBe("broken");
    expect(
      readEntraSettings({ ...good, PUBLIC_URL: "http://localhost:3000", INSECURE_LOCAL_COOKIE: "1" }).kind,
    ).toBe("on");
    expect(
      readEntraSettings({ ...good, PUBLIC_URL: "http://evil.example", INSECURE_LOCAL_COOKIE: "1" }).kind,
    ).toBe("broken");
  });

  it("refuses a tenant or client that could change the address it is put into", () => {
    for (const bad of ["a/b", "a?x=1", "a b", "../x", "a#b", "a@b"]) {
      expect(readEntraSettings({ ...good, ENTRA_TENANT_ID: bad }).kind).toBe("broken");
      expect(readEntraSettings({ ...good, ENTRA_CLIENT_ID: bad }).kind).toBe("broken");
    }
  });

  it("only lets a test sign-in server be used on a local machine", () => {
    const test = { ...good, ENTRA_TEST_AUTHORITY: "http://127.0.0.1:3200/" };
    expect(readEntraSettings(test).kind).toBe("broken");
    const config = on({ ...test, INSECURE_LOCAL_COOKIE: "1", PUBLIC_URL: "http://127.0.0.1:3101" });
    expect(config.authority).toBe("http://127.0.0.1:3200");
  });
});

describe("the sign-in address", () => {
  const config = on(good);

  it("carries every protection Microsoft asks for", () => {
    const url = new URL(authorizeUrl(config, "the-state", "the-challenge"));
    expect(url.origin + url.pathname).toBe(
      `https://login.microsoftonline.com/${TENANT}/oauth2/v2.0/authorize`,
    );
    const q = url.searchParams;
    expect(q.get("client_id")).toBe(CLIENT);
    expect(q.get("response_type")).toBe("code");
    expect(q.get("redirect_uri")).toBe(redirectUri(config));
    expect(q.get("scope")).toBe(`api://${CLIENT}/access_as_user`);
    expect(q.get("state")).toBe("the-state");
    expect(q.get("code_challenge")).toBe("the-challenge");
    expect(q.get("code_challenge_method")).toBe("S256");
    expect(q.get("prompt")).toBe("select_account");
  });

  it("puts the secret and the verifier in the token request, not in the address", () => {
    const body = new URLSearchParams(tokenRequestBody(config, "the-code", "the-verifier"));
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("the-code");
    expect(body.get("code_verifier")).toBe("the-verifier");
    expect(body.get("client_secret")).toBe("a-secret");
    expect(authorizeUrl(config, "s", "c")).not.toContain("a-secret");
  });
});

describe("PKCE and state", () => {
  it("makes a challenge that is the SHA-256 of the verifier, and different every time", () => {
    const a = newPkce();
    const b = newPkce();
    expect(a.challenge).toBe(createHash("sha256").update(a.verifier).digest("base64url"));
    expect(a.verifier).not.toBe(b.verifier);
    expect(a.verifier.length).toBeGreaterThanOrEqual(43);
    expect(newState()).not.toBe(newState());
  });

  it("packs and unpacks an attempt, and rejects rubbish", () => {
    expect(unpackAttempt(packAttempt("s", "v"))).toEqual({ state: "s", verifier: "v" });
    expect(unpackAttempt(undefined)).toBeNull();
    expect(unpackAttempt("not base64 json")).toBeNull();
    expect(unpackAttempt(Buffer.from('{"s":1,"v":2}').toString("base64url"))).toBeNull();
  });

  it("compares secrets exactly", () => {
    expect(sameSecret("abc", "abc")).toBe(true);
    expect(sameSecret("abc", "abd")).toBe(false);
    expect(sameSecret("abc", "abcd")).toBe(false);
  });
});

describe("the token answer", () => {
  const token = "x".repeat(40);

  it("accepts a normal answer and caps the lifetime", () => {
    expect(readTokenResponse({ access_token: token, token_type: "Bearer", expires_in: 3599 })).toEqual({
      token,
      seconds: 3599,
    });
    expect(
      readTokenResponse({ access_token: token, token_type: "bearer", expires_in: 10_000_000 })?.seconds,
    ).toBe(86_400);
  });

  it("refuses anything that is not what was asked for", () => {
    expect(readTokenResponse(null)).toBeNull();
    expect(readTokenResponse({})).toBeNull();
    expect(readTokenResponse({ access_token: "short", token_type: "Bearer", expires_in: 60 })).toBeNull();
    expect(readTokenResponse({ access_token: token, token_type: "MAC", expires_in: 60 })).toBeNull();
    expect(readTokenResponse({ access_token: token, token_type: "Bearer", expires_in: 0 })).toBeNull();
    expect(readTokenResponse({ access_token: token, token_type: "Bearer", expires_in: "60" })).toBeNull();
    expect(readTokenResponse({ access_token: 5, token_type: "Bearer", expires_in: 60 })).toBeNull();
  });
});
