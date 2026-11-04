import re
import time
import uuid
from dataclasses import dataclass
from datetime import date

from opentelemetry import trace
from sqlalchemy import ColumnElement, Text, func, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import Session

from app.logging import content_free_span
from app.models import Chunk, Document, DocumentStatus
from app.schemas import QuestionFilters

_tracer = trace.get_tracer("app.retrieval")


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    document_date: date | None
    superseded: bool  # an older version: lets the model tell versions apart
    page_start: int
    page_end: int
    text_redacted: str
    similarity: float | None
    fts_rank: int | None
    fts_all_terms: bool  # the chunk contains every query term (AND), not just one of them
    score: float


def rrf_fuse(rankings: list[list[uuid.UUID]], k: int = 60) -> list[tuple[uuid.UUID, float]]:
    """Reciprocal Rank Fusion; ties keep first-seen order (sort is stable)."""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


# Policy numbers (TRM-HOG-2024-00123), amounts and years: tokens an exact keyword match nails.
_IDENTIFIER_RE = re.compile(r"\d{3,}")


def has_evidence(hits: list[RetrievedChunk], query_text: str, min_similarity: float) -> bool:
    """A vector hit above the floor, or an FTS hit that is more than one shared common word.

    OR-semantics FTS matches almost any in-domain question ("póliza", "cubre"), so an FTS hit only
    counts when the chunk has every query term, or when the question carries an identifier.
    """
    has_identifier = _IDENTIFIER_RE.search(query_text) is not None
    return any(
        (h.similarity is not None and h.similarity >= min_similarity)
        or (h.fts_rank is not None and (h.fts_all_terms or has_identifier))
        for h in hits
    )


def _filter_conditions(filters: QuestionFilters, embedding_model: str) -> list[ColumnElement[bool]]:
    """Single WHERE builder shared by the vector and FTS queries."""
    conds: list[ColumnElement[bool]] = [
        Document.status == DocumentStatus.ready,
        # Vectors from another embedding model live in a different space: comparing them is noise.
        Document.embedding_model == embedding_model,
        Chunk.suspicious.is_(False),
    ]
    if not filters.include_superseded:
        conds.append(Document.is_current.is_(True))
    if filters.doc_type is not None:
        conds.append(Document.doc_type == filters.doc_type)
    if filters.policy_number is not None:
        conds.append(Document.policy_number == filters.policy_number)
    if filters.date_from is not None:
        conds.append(Document.document_date >= filters.date_from)
    if filters.date_to is not None:
        conds.append(Document.document_date <= filters.date_to)
    return conds


def _vector_search(
    session: Session, embedding: list[float], conds: list[ColumnElement[bool]], limit: int
) -> list[tuple[uuid.UUID, float]]:
    """(chunk_id, similarity) ordered by distance."""
    # Same transaction as the query below so the setting applies to it.
    session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    distance = Chunk.embedding.cosine_distance(embedding).label("distance")
    rows = session.execute(
        select(Chunk.id, distance)
        .join(Document, Chunk.document_id == Document.id)
        .where(*conds)
        .order_by(distance)
        .limit(limit)
    ).all()
    # relaxed_order may return rows slightly out of order, so re-sort by distance.
    return [(cid, 1.0 - dist) for cid, dist in sorted(rows, key=lambda r: r.distance)]


def _fts_search(
    session: Session, query_text: str, conds: list[ColumnElement[bool]], limit: int
) -> list[tuple[uuid.UUID, bool]]:
    """(chunk_id, has every term) ordered by rank."""
    all_terms = func.plainto_tsquery("spanish", query_text)
    # OR semantics for recall: questions rarely have every term in a single chunk.
    any_term = func.replace(all_terms.cast(Text), "&", "|").cast(TSQUERY)
    rows = session.execute(
        select(Chunk.id, Chunk.tsv.op("@@")(all_terms))
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.tsv.op("@@")(any_term), *conds)
        .order_by(func.ts_rank_cd(Chunk.tsv, any_term).desc(), Chunk.ordinal)
        .limit(limit)
    ).all()
    return [(cid, bool(matched)) for cid, matched in rows]


def search(
    session: Session,
    query_text: str,
    query_embedding: list[float],
    embedding_model: str,
    filters: QuestionFilters,
    *,
    k_vector: int = 20,
    k_fts: int = 20,
    top_n: int = 6,
) -> list[RetrievedChunk]:
    started = time.perf_counter()
    with content_free_span(_tracer, "retrieval.search") as span:
        conds = _filter_conditions(filters, embedding_model)
        vector_hits = _vector_search(session, query_embedding, conds, k_vector)
        fts_hits = _fts_search(session, query_text, conds, k_fts)
        similarity = dict(vector_hits)
        fts_all_terms = dict(fts_hits)
        fts_rank = {cid: pos for pos, (cid, _) in enumerate(fts_hits, start=1)}
        fused = rrf_fuse([[cid for cid, _ in vector_hits], [cid for cid, _ in fts_hits]])

        rows = {
            chunk.id: (chunk, document)
            for chunk, document in session.execute(
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(Chunk.id.in_([cid for cid, _ in fused]))
            )
        }
        results = [
            RetrievedChunk(
                chunk_id=cid,
                document_id=rows[cid][0].document_id,
                document_title=rows[cid][1].title,
                document_date=rows[cid][1].document_date,
                superseded=rows[cid][1].superseded_by_id is not None,
                page_start=rows[cid][0].page_start,
                page_end=rows[cid][0].page_end,
                text_redacted=rows[cid][0].text_redacted,
                similarity=similarity.get(cid),
                fts_rank=fts_rank.get(cid),
                fts_all_terms=fts_all_terms.get(cid, False),
                score=score,
            )
            for cid, score in fused
        ]
        ordinals = {cid: rows[cid][0].ordinal for cid in rows}
        results.sort(
            key=lambda r: (
                -r.score,
                -(r.similarity if r.similarity is not None else float("-inf")),
                ordinals[r.chunk_id],
            )
        )
        results = results[:top_n]

        span.set_attribute("retrieval.vector_hits", len(vector_hits))
        span.set_attribute("retrieval.fts_hits", len(fts_hits))
        if vector_hits:
            span.set_attribute("retrieval.top_similarity", vector_hits[0][1])
        span.set_attribute("retrieval.filters", sorted(filters.model_dump(exclude_defaults=True)))
        span.set_attribute("retrieval.latency_ms", (time.perf_counter() - started) * 1000)
    return results
