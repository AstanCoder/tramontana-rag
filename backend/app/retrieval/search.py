import time
import uuid
from dataclasses import dataclass

from opentelemetry import trace
from sqlalchemy import ColumnElement, Text, func, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import Session

from app.logging import content_free_span
from app.models import Chunk, ChunkKind, Document, DocumentStatus
from app.schemas import QuestionFilters

_tracer = trace.get_tracer("app.retrieval")


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    page_start: int
    page_end: int
    kind: ChunkKind
    text: str
    text_redacted: str
    similarity: float | None
    fts_rank: int | None
    score: float


def rrf_fuse(rankings: list[list[uuid.UUID]], k: int = 60) -> list[tuple[uuid.UUID, float]]:
    """Reciprocal Rank Fusion; ties keep first-seen order (sort is stable)."""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def has_evidence(hits: list[RetrievedChunk], min_similarity: float) -> bool:
    # Lax on purpose: the LLM no_evidence flag and the citation validator are the strict guards.
    return any(h.similarity is not None and h.similarity >= min_similarity for h in hits) or any(
        h.fts_rank is not None for h in hits
    )


def _filter_conditions(filters: QuestionFilters) -> list[ColumnElement[bool]]:
    """Single WHERE builder shared by the vector and FTS queries."""
    conds: list[ColumnElement[bool]] = [
        Document.status == DocumentStatus.ready,
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
) -> list[uuid.UUID]:
    # OR semantics: questions rarely have every term in a single chunk.
    tsquery = func.replace(func.plainto_tsquery("spanish", query_text).cast(Text), "&", "|").cast(
        TSQUERY
    )
    return list(
        session.scalars(
            select(Chunk.id)
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.tsv.op("@@")(tsquery), *conds)
            .order_by(func.ts_rank_cd(Chunk.tsv, tsquery).desc(), Chunk.ordinal)
            .limit(limit)
        )
    )


def search(
    session: Session,
    query_text: str,
    query_embedding: list[float],
    filters: QuestionFilters,
    *,
    k_vector: int = 20,
    k_fts: int = 20,
    top_n: int = 6,
) -> list[RetrievedChunk]:
    started = time.perf_counter()
    with content_free_span(_tracer, "retrieval.search") as span:
        conds = _filter_conditions(filters)
        vector_hits = _vector_search(session, query_embedding, conds, k_vector)
        fts_ids = _fts_search(session, query_text, conds, k_fts)
        similarity = dict(vector_hits)
        fts_rank = {cid: pos for pos, cid in enumerate(fts_ids, start=1)}
        fused = rrf_fuse([[cid for cid, _ in vector_hits], fts_ids])

        rows = {
            chunk.id: (chunk, title)
            for chunk, title in session.execute(
                select(Chunk, Document.title)
                .join(Document, Chunk.document_id == Document.id)
                .where(Chunk.id.in_([cid for cid, _ in fused]))
            )
        }
        results = [
            RetrievedChunk(
                chunk_id=cid,
                document_id=rows[cid][0].document_id,
                document_title=rows[cid][1],
                page_start=rows[cid][0].page_start,
                page_end=rows[cid][0].page_end,
                kind=rows[cid][0].kind,
                text=rows[cid][0].text,
                text_redacted=rows[cid][0].text_redacted,
                similarity=similarity.get(cid),
                fts_rank=fts_rank.get(cid),
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
        span.set_attribute("retrieval.fts_hits", len(fts_ids))
        if vector_hits:
            span.set_attribute("retrieval.top_similarity", vector_hits[0][1])
        span.set_attribute("retrieval.filters", sorted(filters.model_dump(exclude_defaults=True)))
        span.set_attribute("retrieval.latency_ms", (time.perf_counter() - started) * 1000)
    return results
