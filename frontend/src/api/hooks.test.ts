import { describe, expect, it } from "vitest";
import { hasPending } from "./hooks";
import type { DocumentList, DocumentOut } from "./types";

const doc = (status: DocumentOut["status"]): DocumentOut => ({
  id: "1",
  title: "t",
  original_filename: "t.pdf",
  doc_type: "policy",
  policy_number: null,
  document_date: null,
  status,
  error_code: null,
  page_count: null,
  chunk_count: 0,
  is_current: true,
  created_at: "2026-01-01T00:00:00Z",
});
const list = (...s: DocumentOut["status"][]): DocumentList => ({ items: s.map(doc) });

describe("hasPending", () => {
  it("is true for pending or processing documents", () => {
    expect(hasPending(list("ready", "pending"))).toBe(true);
    expect(hasPending(list("processing"))).toBe(true);
  });
  it("is false for terminal states or no data", () => {
    expect(hasPending(list("ready", "failed"))).toBe(false);
    expect(hasPending(undefined)).toBe(false);
  });
});
