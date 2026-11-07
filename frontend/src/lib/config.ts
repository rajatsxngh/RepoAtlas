/**
 * App settings from `NEXT_PUBLIC_*` variables. Next.js inlines them at build time, so each one
 * is read by its literal name below.
 */

export const DEFAULT_API_URL = "http://localhost:8000";
export const DEFAULT_NODE_CAP = 300;
const MIN_NODE_CAP = 20;
const MAX_NODE_CAP = 2000;

export interface AppConfig {
  /** Origin of the RepoAtlas API; routes live under `/api/v1`. */
  apiUrl: string;
  /** Serve fixtures from memory instead of calling the API (`NEXT_PUBLIC_API_MOCK=1`). */
  mock: boolean;
  /** At most this many nodes are visible on the map at once. */
  nodeCap: number;
}

export interface ConfigEnv {
  NEXT_PUBLIC_API_URL?: string;
  NEXT_PUBLIC_API_MOCK?: string;
  NEXT_PUBLIC_NODE_CAP?: string;
}

export function readConfig(env: ConfigEnv): AppConfig {
  const apiUrl = (env.NEXT_PUBLIC_API_URL?.trim() || DEFAULT_API_URL).replace(/\/+$/, "");
  const mock = ["1", "true", "yes"].includes((env.NEXT_PUBLIC_API_MOCK ?? "").trim().toLowerCase());
  const cap = Number.parseInt(env.NEXT_PUBLIC_NODE_CAP ?? "", 10);
  const nodeCap = Number.isFinite(cap)
    ? Math.min(MAX_NODE_CAP, Math.max(MIN_NODE_CAP, cap))
    : DEFAULT_NODE_CAP;
  return { apiUrl, mock, nodeCap };
}

export const config: AppConfig = readConfig({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_API_MOCK: process.env.NEXT_PUBLIC_API_MOCK,
  NEXT_PUBLIC_NODE_CAP: process.env.NEXT_PUBLIC_NODE_CAP,
});
