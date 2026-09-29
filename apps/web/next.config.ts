import type { NextConfig } from "next";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

// The monorepo's existing .env stays server-only. Never expose it through nextConfig.env.
const rootEnv = resolve(process.cwd(), "../../.env");
if (process.env.DOCTA_DEV_MANAGED !== "1" && existsSync(rootEnv)) process.loadEnvFile(rootEnv);

const nextConfig: NextConfig = {
  agentRules: false,
  poweredByHeader: false,
  // Development request logs must not retain the callback's short-lived authorization code.
  logging: { incomingRequests: { ignore: [/^\/auth\/callback(?:\?|$)/] } },
};

export default nextConfig;
