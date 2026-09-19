import type { NextConfig } from "next";

/**
 * `/api/*` is proxied to FastAPI by the route handler in `src/app/api/[...path]`
 * rather than by a rewrite here: rewrites are resolved at build time, and the API
 * host is only known at deploy time.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Keeps the client bundle small on an 8 GB / i3 development machine.
  experimental: { optimizePackageImports: [] },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
