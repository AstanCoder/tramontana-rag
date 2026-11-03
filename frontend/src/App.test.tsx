import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const question = (id: string, answer: string) => ({
  id,
  status: "answered",
  created_at: "2026-01-01T00:00:00Z",
  error_code: null,
  trace_id: null,
  result: { status: "answered", answer, citations: [], confidence: "high", degraded: false, reason: null },
});

afterEach(() => vi.unstubAllGlobals());

describe("App assistant", () => {
  it("keeps every asked question with its answer, newest first", async () => {
    let asked = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((path: string, init?: RequestInit) => {
        if (path === "/api/documents") return Promise.resolve(json({ items: [] }));
        if (init?.method === "POST") return Promise.resolve(json({ ...question(`q${++asked}`, ""), status: "pending" }, 202));
        const id = path.split("/").pop()!;
        return Promise.resolve(json(question(id, `Respuesta ${id}`)));
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Asistente" }));
    for (const text of ["¿Primera pregunta?", "¿Segunda pregunta?"]) {
      await user.clear(screen.getByLabelText("Pregunta"));
      await user.type(screen.getByLabelText("Pregunta"), text);
      await user.click(screen.getByRole("button", { name: "Preguntar" }));
      await screen.findByText(text);
    }

    const items = await screen.findAllByRole("article");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("¿Segunda pregunta?");
    await waitFor(() => expect(within(items[0]).getByText("Respuesta q2")).toBeInTheDocument());
    expect(items[1]).toHaveTextContent("¿Primera pregunta?");
    expect(within(items[1]).getByText("Respuesta q1")).toBeInTheDocument();
  });
});
