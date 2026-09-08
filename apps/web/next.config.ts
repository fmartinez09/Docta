import type { NextConfig } from "next";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

// The monorepo's existing .env stays server-only. Never expose it through nextConfig.env.
const rootEnv = resolve(process.cwd(), "../../.env");
if (existsSync(rootEnv)) process.loadEnvFile(rootEnv);

const nextConfig: NextConfig = {
  agentRules: false,
  poweredByHeader: false,
};

export default nextConfig;
