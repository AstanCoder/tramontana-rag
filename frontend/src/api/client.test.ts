import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("apiFetch", () => {
  it("throws ApiError with the envelope code, trace id and status", async () => {
    const body = { error: { code: "file_too_large", message: "Demasiado grande", trace_id: "abc" } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 413 })));
    const err = await apiFetch("/api/documents").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code: "file_too_large", traceId: "abc", status: 413, message: "Demasiado grande" });
  });

  it("falls back to internal_error when the body is not an envelope", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 502 })));
    await expect(apiFetch("/api/x")).rejects.toMatchObject({ code: "internal_error", status: 502 });
  });

  it("returns parsed JSON on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] }))));
    await expect(apiFetch("/api/documents")).resolves.toEqual({ items: [] });
  });
});
