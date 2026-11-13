import { forward } from "../../lib/proxy";

export const dynamic = "force-dynamic";

// Only active when the simulation lab is running (scripts/sim.py sets SIM_PROXY_TARGET).
const SIM_TARGET = (process.env.SIM_PROXY_TARGET || "").replace(/\/$/, "");

async function handler(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  if (!SIM_TARGET) return Response.json({ detail: "The simulation lab is not running." }, { status: 404 });
  const { path } = await params;
  const search = new URL(request.url).search;
  // The simulator serves its control API under /sim and its fake GitHub pages under /github.
  const upstreamPath = path[0] === "github" ? path : ["sim", ...path];
  return forward(request, `${SIM_TARGET}/${upstreamPath.map(encodeURIComponent).join("/")}${search}`);
}

export { handler as GET, handler as POST, handler as DELETE };
