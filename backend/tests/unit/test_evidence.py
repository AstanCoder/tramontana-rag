import uuid

import pytest

from app.retrieval.search import RetrievedChunk, has_evidence


def _hit(similarity: float | None, fts_rank: int | None, all_terms: bool = False) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="Póliza",
        page_start=1,
        page_end=1,
        text_redacted="texto",
        similarity=similarity,
        fts_rank=fts_rank,
        fts_all_terms=all_terms,
        score=0.01,
    )


def test_similarity_above_floor_is_evidence() -> None:
    assert has_evidence([_hit(0.6, None)], "¿Qué cubre?", 0.55)
    assert not has_evidence([_hit(0.5, None)], "¿Qué cubre?", 0.55)


def test_fts_hit_sharing_one_common_word_is_not_evidence() -> None:
    hits = [_hit(0.3, 1), _hit(None, 2)]
    assert not has_evidence(hits, "¿Cubre la póliza al veterinario de mi perro?", 0.55)


def test_fts_hit_with_every_term_is_evidence() -> None:
    assert has_evidence([_hit(None, 1, all_terms=True)], "deducible daños por agua", 0.55)


@pytest.mark.parametrize(
    "question", ["¿Qué cubre la póliza TRM-HOG-2024-00123?", "¿Qué pasa con la factura 4512?"]
)
def test_any_fts_hit_is_evidence_when_question_has_an_identifier(question: str) -> None:
    assert has_evidence([_hit(None, 1)], question, 0.55)


def test_no_hits_no_evidence() -> None:
    assert not has_evidence([], "TRM-HOG-2024-00123", 0.0)
