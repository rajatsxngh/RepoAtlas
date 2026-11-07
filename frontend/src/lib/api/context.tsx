"use client";

/** The API client components use: the app's own, or one a test or story provides. */

import { createContext, useContext, type ReactNode } from "react";
import type { ApiClient } from "./client";
import { getApiClient } from "./index";

const ApiContext = createContext<ApiClient | null>(null);

export function ApiProvider({ client, children }: { client: ApiClient; children: ReactNode }) {
  return <ApiContext.Provider value={client}>{children}</ApiContext.Provider>;
}

export function useApi(): ApiClient {
  return useContext(ApiContext) ?? getApiClient();
}
