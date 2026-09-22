import { createHash, randomBytes } from "node:crypto";
import { NextResponse } from "next/server";
import { authConfig, canonicalLoginUrl, cookieOptions, discovery, seal } from "@/lib/auth";

export async function GET(request: Request) {
  try {
    const config = authConfig();
    // Create the PKCE cookie on the same origin that will receive the callback.
    const canonical = canonicalLoginUrl(request, config.origin);
    if (canonical) {
      const response = NextResponse.redirect(canonical);
      response.headers.set("Cache-Control", "no-store");
      return response;
    }
    const endpoints = await discovery(config);
    const state = randomBytes(32).toString("base64url");
    const verifier = randomBytes(32).toString("base64url");
    const target = new URL(endpoints.authorization_endpoint);
    const values = {
      response_type: "code",
      client_id: config.clientId,
      redirect_uri: config.callback,
      scope: config.scopes,
      state,
      code_challenge_method: "S256",
      code_challenge: createHash("sha256").update(verifier).digest("base64url"),
    };
    for (const [key, value] of Object.entries(values))
      target.searchParams.set(key, value);
    const response = NextResponse.redirect(target);
    response.cookies.set(
      "docta_flow",
      seal(
        { kind: "flow", state, verifier, expires: Date.now() + 600_000 },
        config.secret,
      ),
      cookieOptions(config.origin, 600),
    );
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    return response;
  } catch {
    return NextResponse.redirect(
      new URL("/?error=login_unavailable", request.url),
    );
  }
}
