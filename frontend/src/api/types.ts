// Mirrors backend/app/schemas.py and backend/app/models.py.
export type DocType = "policy" | "general_conditions" | "claim_letter" | "other";
export type DocumentStatus = "pending" | "processing" | "failed" | "ready";
export type QuestionStatus = "pending" | "processing" | "answered" | "no_evidence" | "failed";
export type Confidence = "high" | "medium" | "low";

export interface DocumentOut {
  id: string;
  title: string;
  original_filename: string;
  doc_type: DocType;
  policy_number: string | null;
  document_date: string | null;
  status: DocumentStatus;
  error_code: string | null;
  page_count: number | null;
  chunk_count: number;
  is_current: boolean;
  created_at: string;
}

export interface DocumentList {
  items: DocumentOut[];
}

export interface DocumentFilters {
  doc_type?: DocType;
  policy_number?: string;
  status?: DocumentStatus;
}

export interface QuestionFilters {
  doc_type?: DocType;
  policy_number?: string;
  date_from?: string;
  date_to?: string;
  include_superseded?: boolean;
}

export interface QuestionIn {
  question: string;
  filters: QuestionFilters;
}

export interface Citation {
  document_id: string;
  document_title: string;
  page: number;
  page_end: number | null;
  fragment: string;
  chunk_id: string;
}

export interface AnswerOut {
  status: "answered" | "no_evidence";
  answer: string | null;
  citations: Citation[];
  confidence: Confidence | null;
  degraded: boolean;
  reason: string | null;
}

export interface QuestionOut {
  id: string;
  status: QuestionStatus;
  created_at: string;
  result: AnswerOut | null;
  error_code: string | null;
  trace_id: string | null;
}
