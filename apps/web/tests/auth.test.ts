import { describe, expect, it } from "vitest";
import {
  authConfig,
  canonicalLoginUrl,
  cookieOptions,
  sameOrigin,
  seal,
  unseal,
} from "../lib/auth";
import { allowedProxyPath } from "../lib/proxy-policy";

const secret = "a-test-secret-with-at-least-32-characters";

describe("server session boundary", () => {
  it("canonicalizes login before placing a host-scoped PKCE cookie", () => {
    const origin = "http://127.0.0.1:3100";
    const request = (host: string) => new Request("http://localhost:3100/auth/login", { headers: { host } });
    expect(canonicalLoginUrl(request("127.0.0.1:3100"), origin)).toBeNull();
    expect(canonicalLoginUrl(request("localhost:3100"), origin))
      .toBe(`${origin}/auth/login`);
    expect(canonicalLoginUrl(request("untrusted.test"), origin))
      .toBe(`${origin}/auth/login`);
  });
  it("encrypts the token, detects tampering and rejects expired cookies", () => {
    const session = {
      kind: "session" as const,
      token: "private-bearer-token",
      expires: Date.now() + 60000,
    };
    const cookie = seal(session, secret);
    expect(cookie).not.toContain(session.token);
    expect(unseal(cookie, secret)).toEqual(session);
    expect(unseal(cookie, "different-secret")).toBeNull();
    const bytes = Buffer.from(cookie, "base64url");
    bytes[30] ^= 1;
    expect(unseal(bytes.toString("base64url"), secret)).toBeNull();
    expect(
      unseal(seal({ ...session, expires: Date.now() - 1 }, secret), secret),
    ).toBeNull();
    expect(unseal("not-a-cookie", secret)).toBeNull();
  });
  it("requires an explicit secret and protects cookie/CSRF boundaries", () => {
    expect(() => authConfig({})).toThrow();
    expect(cookieOptions("https://docta.test", 60)).toMatchObject({
      httpOnly: true,
      secure: true,
      sameSite: "lax",
    });
    expect(
      sameOrigin(
        new Request("https://docta.test/api", {
          headers: { Origin: "https://evil.test" },
        }),
        "https://docta.test",
      ),
    ).toBe(false);
    expect(
      sameOrigin(new Request("https://docta.test/api"), "https://docta.test"),
    ).toBe(false);
  });
  it("does not expose arbitrary API paths through the authenticated proxy", () => {
    expect(allowedProxyPath("GET", "courses")).toBe(true);
    for (const path of [
      "../admin",
      "courses/../../admin",
      "https://evil.test",
      "health",
      "courses?token=x",
    ]) {
      expect(allowedProxyPath("GET", path)).toBe(false);
    }
    expect(allowedProxyPath("DELETE", "courses")).toBe(false);
  });
});
