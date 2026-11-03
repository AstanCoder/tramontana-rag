import uuid

import pytest

from app.models import ChunkKind
from app.retrieval.search import RetrievedChunk
from app.schemas import AnswerOut, Citation
from eval.run_eval import (
    citation_faithfulness,
    hit_at_k,
    keyword_recall,
    reciprocal_rank,
    refusal_precision_recall,
)


def _hit(title: str, page: int, text: str = "texto", page_end: int | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title=title,
        page_start=page,
        page_end=page_end or page,
        kind=ChunkKind.text,
        text=text,
        text_redacted=text,
        similarity=0.9,
        fts_rank=1,
        score=0.03,
    )


def _cite(hit: RetrievedChunk, fragment: str, page: int | None = None) -> Citation:
    return Citation(
        document_id=hit.document_id,
        document_title=hit.document_title,
        page=page or hit.page_start,
        fragment=fragment,
        chunk_id=hit.chunk_id,
    )


HITS = [_hit("A", 1), _hit("B", 2), _hit("B", 3, page_end=4), _hit("C", 1)]


def test_hit_at_k_matches_document_and_page_within_k() -> None:
    assert hit_at_k(HITS, "B", 2, 6)
    assert hit_at_k(HITS, "B", 4, 6)  # inside the chunk's page span
    assert not hit_at_k(HITS, "B", 2, 1)  # the match is ranked 2nd
    assert not hit_at_k(HITS, "B", 5, 6)
    assert not hit_at_k(HITS, "D", 1, 6)


def test_hit_at_k_without_page_matches_document_only() -> None:
    assert hit_at_k(HITS, "C", None, 6)


def test_reciprocal_rank_is_inverse_of_first_match_rank() -> None:
    assert reciprocal_rank(HITS, "A", 1, 6) == 1.0
    assert reciprocal_rank(HITS, "B", 3, 6) == pytest.approx(1 / 3)
    assert reciprocal_rank(HITS, "B", 3, 2) == 0.0
    assert reciprocal_rank(HITS, "Z", 1, 6) == 0.0


def test_citation_faithfulness_counts_fragment_in_chunk_and_matching_page() -> None:
    hit = _hit("A", 2, text="Daños por agua: deducible 300 €.")
    other = _hit("B", 3, text="Incendio: sin deducible.")
    answer = AnswerOut(
        status="answered",
        answer="x",
        citations=[
            _cite(hit, "deducible   300 €"),  # whitespace-normalised match
            _cite(other, "Incendio: sin deducible.", page=9),  # wrong page
            _cite(hit, "texto inventado"),  # fragment not in the chunk
            Citation(  # chunk not among the hits
                document_id=uuid.uuid4(),
                document_title="Z",
                page=1,
                fragment="x",
                chunk_id=uuid.uuid4(),
            ),
        ],
    )
    assert citation_faithfulness(answer, [hit, other]) == pytest.approx(0.25)


def test_citation_faithfulness_is_zero_without_citations() -> None:
    assert citation_faithfulness(AnswerOut(status="no_evidence"), HITS) == 0.0


def test_keyword_recall_ignores_case_accents_and_spacing() -> None:
    assert keyword_recall("Deducible de 300 € por DAÑOS", ["300 €", "danos", "robo"]) == (
        pytest.approx(2 / 3)
    )
    assert keyword_recall("", ["x"]) == 0.0
    assert keyword_recall("lo que sea", []) == 1.0


def test_refusal_precision_recall() -> None:
    pairs = [
        ("no_evidence", "no_evidence"),
        ("no_evidence", "answered"),
        ("answered", "no_evidence"),
        ("answered", "answered"),
    ]
    assert refusal_precision_recall(pairs) == (0.5, 0.5)
    assert refusal_precision_recall([("answered", "answered")]) == (1.0, 1.0)  # vacuous
