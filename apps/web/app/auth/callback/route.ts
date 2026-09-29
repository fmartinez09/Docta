import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { authConfig, cookieOptions, discovery, seal, unseal } from "@/lib/auth";
import { readWebConfig } from "@/lib/config";

export async function GET(request: Request) {
  try {
    const config = authConfig();
    const params = new URL(request.url).searchParams;
    const flow = unseal(
      (await cookies()).get("docta_flow")?.value,
      config.secret,
    );
    if (
      flow?.kind !== "flow" ||
      params.getAll("state").length !== 1 ||
      params.get("state") !== flow.state ||
      params.getAll("code").length !== 1 ||
      !params.get("code") ||
      params.has("error")
    )
      throw new Error("Invalid callback");
    const endpoints = await discovery(config);
    const exchange = await fetch(endpoints.token_endpoint, {
      method: "POST",
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
      body: new URLSearchParams({
        grant_type: "authorization_code",
        client_id: config.clientId,
        redirect_uri: config.callback,
        code: params.get("code")!,
        code_verifier: flow.verifier,
      }),
    });
    if (!exchange.ok) throw new Error("Exchange failed");
    const token = await exchange.json();
    if (
      typeof token.access_token !== "string" ||
      token.token_type?.toLowerCase() !== "bearer" ||
      !Number.isFinite(token.expires_in) ||
      token.expires_in <= 0
    )
      throw new Error("Invalid token response");
    // FastAPI's IdentityProvider verifies signature, issuer, audience and lifetime before login succeeds.
    const verified = await fetch(
      `${readWebConfig().apiBaseUrl}/api/v1/courses`,
      {
        headers: { Authorization: `Bearer ${token.access_token}` },
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(10000),
      },
    );
    if (!verified.ok) throw new Error("Token rejected");
    const seconds = Math.min(token.expires_in, 3600);
    const session = seal(
      {
        kind: "session",
        token: token.access_token,
        expires: Date.now() + seconds * 1000,
      },
      config.secret,
    );
    if (session.length > 3800) throw new Error("Session too large");
    const response = NextResponse.redirect(`${config.origin}/`);
    response.cookies.set(
      "docta_session",
      session,
      cookieOptions(config.origin, seconds),
    );
    response.cookies.set("docta_flow", "", cookieOptions(config.origin, 0));
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    return response;
  } catch {
    const response = NextResponse.redirect(
      new URL("/?error=login_failed", request.url),
    );
    response.cookies.delete("docta_flow");
    return response;
  }
}
