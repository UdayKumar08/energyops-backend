import { NextResponse, type NextRequest } from "next/server";
import { backendPathFor, isSameOrigin, SAFE_METHODS } from "@/lib/proxy-path";
import { backendUrl, readToken } from "@/lib/session";

// Forwards the browser's calls to the backend with the person's token added. The token lives in
// an httpOnly cookie, so page scripts never see it.

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, context: Context): Promise<NextResponse> {
  const { path } = await context.params;
  const target = backendPathFor(path);
  if (target === null) return NextResponse.json({ detail: "Not found." }, { status: 404 });
  if (!SAFE_METHODS.has(request.method) && !isSameOrigin(request.headers)) {
    return NextResponse.json({ detail: "That request did not come from this site." }, { status: 403 });
  }
  const token = await readToken();
  if (token === null) return NextResponse.json({ detail: "Sign in first." }, { status: 401 });

  const headers = new Headers({ Authorization: `Bearer ${token}` });
  const type = request.headers.get("content-type");
  if (type) headers.set("content-type", type);
  const hasBody = !SAFE_METHODS.has(request.method);
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl() + target + request.nextUrl.search, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "The service could not be reached." }, { status: 502 });
  }
  const out = new Headers();
  const upstreamType = upstream.headers.get("content-type");
  if (upstreamType) out.set("content-type", upstreamType);
  out.set("cache-control", "no-store");
  return new NextResponse(upstream.status === 204 ? null : upstream.body, {
    status: upstream.status,
    headers: out,
  });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
