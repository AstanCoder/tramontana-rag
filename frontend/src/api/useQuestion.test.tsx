import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useQuestion } from "./hooks";
import type { QuestionOut } from "./types";

const q = (status: QuestionOut["status"]): QuestionOut => ({
  id: "q1",
  status,
  created_at: "2026-01-01T00:00:00Z",
  result: null,
  error_code: null,
  trace_id: null,
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useQuestion("q1"), { wrapper });
}

afterEach(() => vi.unstubAllGlobals());

describe("useQuestion", () => {
  it("polls until a terminal status, then stops", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(json(q("processing"))))
      .mockImplementation(() => Promise.resolve(json(q("answered"))));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = setup();
    await waitFor(() => expect(result.current.data?.status).toBe("answered"), { timeout: 3000 });
    const calls = fetchMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 1300));
    expect(fetchMock.mock.calls.length).toBe(calls);
  });

  it("stops polling and exposes the error when a refetch fails", async () => {
    const body = { error: { code: "internal_error", message: "Error interno", trace_id: "t1" } };
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(json(q("processing"))))
      .mockImplementation(() => Promise.resolve(json(body, 500)));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = setup();
    await waitFor(() => expect(result.current.error).toMatchObject({ code: "internal_error" }), { timeout: 3000 });
    const calls = fetchMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 1300));
    expect(fetchMock.mock.calls.length).toBe(calls);
  });
});
