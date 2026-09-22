import {
  createCipheriv,
  createDecipheriv,
  createHash,
  randomBytes,
} from "node:crypto";

type Environment = Record<string, string | undefined>;

export function canonicalLoginUrl(request: Request, origin: string): string | null {
  // Next can construct request.url with its internal hostname. Compare the actual
  // Host, and use only the configured origin as a redirect destination.
  return request.headers.get("host")?.toLowerCase() === new URL(origin).host.toLowerCase()
    ? null : `${origin}/auth/login`;
}
export type Session = { kind: "session"; token: string; expires: number };
export type Flow = {
  kind: "flow";
  state: string;
  verifier: string;
  expires: number;
};

function httpUrl(value: string): URL {
  const url = new URL(value);
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    (url.protocol !== "https:" &&
      !(
        url.protocol === "http:" &&
        ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
      ))
  ) {
    throw new Error("Invalid web authentication configuration");
  }
  return url;
}

export function authConfig(env: Environment = process.env) {
  const origin = httpUrl(
    env.DOCTA_WEB_ORIGIN ?? "http://127.0.0.1:3000",
  ).origin;
  const issuer = env.DOCTA_OIDC_ISSUER;
  const clientId = env.DOCTA_WEB_OIDC_CLIENT_ID ?? env.DOCTA_DEV_OIDC_CLIENT_ID;
  const secret = env.DOCTA_WEB_SESSION_SECRET;
  if (!issuer || !clientId || !secret || secret.length < 32) {
    throw new Error("Web authentication is not configured");
  }
  httpUrl(issuer);
  return {
    origin,
    issuer,
    clientId,
    secret,
    callback: `${origin}/auth/callback`,
    scopes:
      env.DOCTA_WEB_OIDC_SCOPES ??
      `openid profile urn:zitadel:iam:org:project:id:${env.DOCTA_OIDC_AUDIENCE}:aud`,
  };
}

export function seal(value: Session | Flow, secret: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv(
    "aes-256-gcm",
    createHash("sha256").update(secret).digest(),
    iv,
  );
  cipher.setAAD(Buffer.from("docta-web-v1"));
  const encrypted = Buffer.concat([
    cipher.update(JSON.stringify(value), "utf8"),
    cipher.final(),
  ]);
  return Buffer.concat([iv, cipher.getAuthTag(), encrypted]).toString(
    "base64url",
  );
}

export function unseal(
  value: string | undefined,
  secret: string,
): Session | Flow | null {
  if (!value || value.length > 4000) return null;
  try {
    const data = Buffer.from(value, "base64url");
    const cipher = createDecipheriv(
      "aes-256-gcm",
      createHash("sha256").update(secret).digest(),
      data.subarray(0, 12),
    );
    cipher.setAAD(Buffer.from("docta-web-v1"));
    cipher.setAuthTag(data.subarray(12, 28));
    const decoded = JSON.parse(
      Buffer.concat([
        cipher.update(data.subarray(28)),
        cipher.final(),
      ]).toString(),
    );
    if (!Number.isFinite(decoded.expires) || decoded.expires <= Date.now())
      return null;
    if (
      decoded.kind === "session" &&
      typeof decoded.token === "string" &&
      decoded.token
    )
      return decoded;
    if (
      decoded.kind === "flow" &&
      typeof decoded.state === "string" &&
      typeof decoded.verifier === "string"
    )
      return decoded;
  } catch {
    /* Invalid/expired cookies fail closed, without logging secrets. */
  }
  return null;
}

export async function discovery(config: ReturnType<typeof authConfig>) {
  const response = await fetch(
    `${config.issuer.replace(/\/$/, "")}/.well-known/openid-configuration`,
    { cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000) },
  );
  if (!response.ok) throw new Error("Identity unavailable");
  const document = await response.json();
  if (
    document.issuer !== config.issuer ||
    !document.code_challenge_methods_supported?.includes("S256") ||
    !document.token_endpoint_auth_methods_supported?.includes("none")
  )
    throw new Error("Invalid discovery");
  httpUrl(document.authorization_endpoint);
  httpUrl(document.token_endpoint);
  return document as {
    issuer: string;
    authorization_endpoint: string;
    token_endpoint: string;
  };
}

export function cookieOptions(origin: string, maxAge: number) {
  return {
    httpOnly: true,
    secure: origin.startsWith("https:"),
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

export function sameOrigin(request: Request, origin: string) {
  return request.headers.get("origin") === origin;
}
