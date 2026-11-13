import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  // Hostnames (besides localhost) that may load the dev server, e.g. a tunnel or cloud preview URL.
  allowedDevOrigins: (process.env.ALLOWED_DEV_ORIGINS || "").split(",").map(value => value.trim()).filter(Boolean),
};

export default nextConfig;
