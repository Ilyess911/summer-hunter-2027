import path from "node:path"

import type { NextConfig } from "next"

// Local mode: the Python API runs on 127.0.0.1:8800 and is reached through this same-origin rewrite,
// so the session cookie never crosses origins. Hosted mode (Vercel): /api is served by the Python function.
const apiUrl = process.env.SH_API_URL

const nextConfig: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  // A package.json higher up (home folder) must not be taken for the workspace root.
  turbopack: {
    root: path.resolve(__dirname),
    rules: { "*.css": { loaders: ["@tailwindcss/turbopack"], as: "*.css" } },
  },
  async rewrites() {
    return apiUrl ? [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }] : []
  },
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Frame-Options", value: "DENY" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      ],
    }]
  },
}

export default nextConfig
