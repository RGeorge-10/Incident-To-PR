// Server-side reverse proxy used by the /api and /sim route handlers. The browser only ever talks
// to the Next.js origin, so the dashboard works behind any hostname without CORS configuration.
const HOP_BY_HOP = new Set(["connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "host"]);

export async function forward(request: Request, target: string): Promise<Response> {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase()) && key.toLowerCase() !== "cookie") headers.set(key, value);
  });
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  try {
    const upstream = await fetch(target, {
      method: request.method, headers, body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual", cache: "no-store",
    });
    const responseHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
      if (!HOP_BY_HOP.has(key.toLowerCase())) responseHeaders.set(key, value);
    });
    return new Response(upstream.status === 204 || upstream.status === 304 ? null : upstream.body, {
      status: upstream.status, headers: responseHeaders,
    });
  } catch {
    return Response.json({ detail: "The control-plane API is not reachable. Start the API server, then retry." }, { status: 502 });
  }
}
