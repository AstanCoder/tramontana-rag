import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, postJson } from "./client";
import type { DocumentFilters, DocumentList, DocumentOut, QuestionIn, QuestionOut } from "./types";

export const hasPending = (list?: DocumentList): boolean =>
  !!list?.items.some((d) => d.status === "pending" || d.status === "processing");

export function useDocuments(filters: DocumentFilters = {}) {
  const qs = new URLSearchParams(
    Object.entries(filters).filter((e): e is [string, string] => !!e[1]),
  ).toString();
  return useQuery({
    queryKey: ["documents", filters],
    queryFn: () => apiFetch<DocumentList>(`/api/documents${qs ? `?${qs}` : ""}`),
    refetchInterval: (q) => (hasPending(q.state.data) ? 2000 : false),
  });
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (form: FormData) =>
      apiFetch<DocumentOut>("/api/documents", { method: "POST", body: form }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export function useRetryDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => postJson<DocumentOut>(`/api/documents/${id}/retry`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export function useAskQuestion() {
  return useMutation({
    mutationFn: (body: QuestionIn) => postJson<QuestionOut>("/api/questions", body),
  });
}

export function useQuestion(id: string | null) {
  return useQuery({
    queryKey: ["question", id],
    queryFn: () => apiFetch<QuestionOut>(`/api/questions/${id}`),
    enabled: id !== null,
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status === "pending" || status === "processing" ? 1000 : false;
    },
  });
}
