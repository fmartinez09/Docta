import { NextResponse } from "next/server";
import { authConfig, sameOrigin } from "@/lib/auth";

export async function POST(request: Request) {
  const config = authConfig();
  if (!sameOrigin(request, config.origin))
    return new Response(null, { status: 403 });
  const response = NextResponse.redirect(config.origin, 303);
  response.cookies.delete("docta_session");
  response.cookies.delete("docta_flow");
  response.headers.set("Cache-Control", "no-store");
  return response;
}
