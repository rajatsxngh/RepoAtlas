import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { ApiClient } from "@/lib/api/client";
import { ApiProvider } from "@/lib/api/context";
import { MockBackend, type MockBackendOptions } from "@/lib/api/mock/backend";
import { createMockFetch } from "@/lib/api/mock/fetch";

/** An API client over the mock backend, with no latency and instant 429 retries. */
export function mockApi(options: MockBackendOptions = {}) {
  const backend = new MockBackend({ queueMs: 20, buildMs: 120, ...options });
  const api = new ApiClient({
    baseUrl: "http://mock.repoatlas.invalid/api/v1",
    fetch: createMockFetch(backend, { latencyMs: 0 }),
    sleep: async () => {},
  });
  return { api, backend };
}

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });
}

export function renderWithApi(
  ui: ReactElement,
  {
    api = mockApi().api,
    queryClient = testQueryClient(),
    ...options
  }: { api?: ApiClient; queryClient?: QueryClient } & RenderOptions = {},
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ApiProvider client={api}>{children}</ApiProvider>
      </QueryClientProvider>
    );
  }
  return { ...render(ui, { wrapper: Wrapper, ...options }), api, queryClient };
}
