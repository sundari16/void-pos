import type { NextConfig } from "next";

// The web server proxies /api/* to the API, so a till only ever talks to the origin that
// served it: one port to open at the venue, and no CORS setup per venue address.
const apiUrl = (process.env.VOIDPOS_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/:path*` }];
  },
};

export default nextConfig;
