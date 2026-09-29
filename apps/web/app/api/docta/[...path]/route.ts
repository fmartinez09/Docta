import { randomUUID } from "node:crypto";
import { cookies } from "next/headers";
import { authConfig, sameOrigin, unseal } from "@/lib/auth";
import { readWebConfig } from "@/lib/config";
import { allowedProxyPath } from "@/lib/proxy-policy";

function error(status: number, code: string) {
  return Response.json(
    { code },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

async function proxy(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  try {
    const config = authConfig();
    const path = (await context.params).path.join("/");
    if (!allowedProxyPath(request.method, path)) return error(404, "not_found");
    if (request.method !== "GET" && !sameOrigin(request, config.origin))
      return error(403, "origin_denied");
    const session = unseal(
      (await cookies()).get("docta_session")?.value,
      config.secret,
    );
    if (session?.kind !== "session")
      return error(401, "authentication_required");
    let body: string | undefined;
    const headers = new Headers({
      Authorization: `Bearer ${session.token}`,
      "X-Request-ID": randomUUID(),
    });
    if (request.method !== "GET") {
      const key = request.headers.get("idempotency-key");
      if (!key?.trim() || key.length > 255)
        return error(400, "idempotency_key_required");
      headers.set("Idempotency-Key", key);
      // Only small JSON commands cross this BFF. PDF bytes go directly to object storage.
      if (Number(request.headers.get("content-length") ?? 0) > 32768)
        return error(413, "request_too_large");
      const reader = request.body?.getReader();
      if (reader) {
        const chunks: Uint8Array[] = [];
        let size = 0;
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          size += chunk.value.byteLength;
          if (size > 32768) {
            await reader.cancel();
            return error(413, "request_too_large");
          }
          chunks.push(chunk.value);
        }
        body = Buffer.concat(chunks).toString("utf8");
        if (body) {
          if (
            !request.headers.get("content-type")?.startsWith("application/json")
          )
            return error(415, "invalid_request");
          headers.set("Content-Type", "application/json");
        }
      }
    }
    const query = new URL(request.url).searchParams;
    const search = new URLSearchParams();
    for (const key of ["after_sequence", "limit"])
      if (query.has(key)) search.set(key, query.get(key)!);
    const target = `${readWebConfig().apiBaseUrl}/api/v1/${path}?${search}`;
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(110_000),
    });
    const responseHeaders = new Headers({
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/json",
      "Cache-Control": "no-store",
      "X-Accel-Buffering": "no",
    });
    const requestId = upstream.headers.get("x-request-id");
    if (requestId) responseHeaders.set("X-Request-ID", requestId);
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return error(503, "service_unavailable");
  }
}

export const GET = proxy;
export const POST = proxy;
