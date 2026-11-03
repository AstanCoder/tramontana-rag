import type { DocType, DocumentStatus } from "./api/types";

export const DOC_TYPE_LABELS: Record<DocType, string> = {
  policy: "Póliza",
  general_conditions: "Condicionado general",
  claim_letter: "Carta de siniestro",
  other: "Otro",
};

export const STATUS_LABELS: Record<DocumentStatus, string> = {
  pending: "Pendiente",
  processing: "Procesando",
  ready: "Listo",
  failed: "Error",
};

// Document/question row `error_code` values (rows carry no message). HTTP errors use the envelope message.
export const ERROR_LABELS: Record<string, string> = {
  corrupt_document: "El PDF está dañado.",
  unreadable_document: "No se pudo leer el contenido del PDF.",
  provider_unavailable: "El proveedor de IA no está disponible.",
  ingestion_error: "Error al procesar el documento.",
  ingestion_timeout: "El procesamiento del documento tardó demasiado.",
  enqueue_failed: "No se pudo encolar el trabajo.",
  answer_timeout: "La respuesta tardó demasiado tiempo.",
  answer_error: "No se pudo generar la respuesta.",
};

export const errorLabel = (code: string | null, fallback = "Ha ocurrido un error."): string =>
  (code && ERROR_LABELS[code]) || fallback;
