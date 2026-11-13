import { forward } from "../../lib/proxy";

export const dynamic = "force-dynamic";

const API_TARGET = (process.env.API_PROXY_TARGET || "http://127.0.0.1:8000").replace(/\/$/, "");

async function handler(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const search = new URL(request.url).search;
  return forward(request, `${API_TARGET}/api/${path.map(encodeURIComponent).join("/")}${search}`);
}

export { handler as GET, handler as POST, handler as PUT, handler as DELETE };
