export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public traceId: string | null,
    public status: number,
  ) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    // Envelope: {"error": {"code", "message", "trace_id"}}; a proxy error may not be JSON.
    const body = (await res.json().catch(() => null)) as {
      error?: { code?: string; message?: string; trace_id?: string | null };
    } | null;
    const e = body?.error;
    throw new ApiError(
      e?.code ?? "internal_error",
      e?.message ?? `Error del servidor (HTTP ${res.status}).`,
      e?.trace_id ?? null,
      res.status,
    );
  }
  return (await res.json()) as T;
}

export const postJson = <T>(path: string, body: unknown) =>
  apiFetch<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
