import uuid

from app.answering.citations import extractive_answer, normalize_for_match, validate_answer
from app.models import ChunkKind
from app.retrieval.search import RetrievedChunk
from app.schemas import LLMAnswer, LLMCitation

WATER = "Daños por agua: capital asegurado 30.000 €, deducible 300 €."
FIRE = "Incendio: capital asegurado 150.000 €, sin deducible."


def _hit(text: str, page: int = 2) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="Póliza hogar",
        page_start=page,
        page_end=page,
        kind=ChunkKind.text,
        text=text,
        text_redacted=text,
        similarity=0.9,
        fts_rank=1,
        score=0.03,
    )


def _llm(*citations: tuple[str, str], answer: str = "El deducible es 300 €.") -> LLMAnswer:
    return LLMAnswer(
        answer=answer,
        citations=[LLMCitation(chunk_id=c, quote=q) for c, q in citations],
        confidence="high",
        no_evidence=False,
    )


def test_valid_citation_kept_with_server_page() -> None:
    hit = _hit(WATER, page=2)
    out = validate_answer(_llm((str(hit.chunk_id), "deducible 300 €")), [hit])
    assert out.status == "answered"
    assert out.answer == "El deducible es 300 €."
    assert out.confidence == "high"
    [citation] = out.citations
    assert citation.chunk_id == hit.chunk_id
    assert citation.document_id == hit.document_id
    assert citation.document_title == "Póliza hogar"
    assert citation.page == 2
    assert citation.fragment == "deducible 300 €"


def test_unknown_chunk_dropped() -> None:
    hit = _hit(WATER)
    out = validate_answer(
        _llm((str(uuid.uuid4()), "deducible 300 €"), (str(hit.chunk_id), "deducible 300 €")),
        [hit],
    )
    assert [c.chunk_id for c in out.citations] == [hit.chunk_id]


def test_fabricated_quote_dropped() -> None:
    water, fire = _hit(WATER), _hit(FIRE)
    out = validate_answer(
        _llm((str(water.chunk_id), "deducible 0 € en todo"), (str(fire.chunk_id), "sin deducible")),
        [water, fire],
    )
    assert [c.chunk_id for c in out.citations] == [fire.chunk_id]


def test_short_quote_dropped() -> None:
    hit = _hit(WATER)
    out = validate_answer(_llm((str(hit.chunk_id), "300 €")), [hit])
    assert out.status == "no_evidence"


def test_whitespace_and_case_insensitive_match() -> None:
    assert normalize_for_match("  DAÑOS   por\nAgua ") == "daños por agua"
    hit = _hit(WATER)
    out = validate_answer(_llm((str(hit.chunk_id), "  DAÑOS   por\n agua: CAPITAL")), [hit])
    assert out.status == "answered"
    assert len(out.citations) == 1


def test_duplicate_chunk_citations_deduped() -> None:
    hit = _hit(WATER)
    out = validate_answer(
        _llm((str(hit.chunk_id), "deducible 300 €"), (str(hit.chunk_id), "capital asegurado")),
        [hit],
    )
    assert len(out.citations) == 1
    assert out.confidence == "high"


def test_no_valid_citations_becomes_no_evidence() -> None:
    hit = _hit(WATER)
    out = validate_answer(_llm((str(hit.chunk_id), "texto que no aparece")), [hit])
    assert out.status == "no_evidence"
    assert out.answer is None
    assert out.citations == []
    assert out.reason


def test_llm_no_evidence_respected() -> None:
    hit = _hit(WATER)
    llm = _llm((str(hit.chunk_id), "deducible 300 €")).model_copy(update={"no_evidence": True})
    out = validate_answer(llm, [hit])
    assert out.status == "no_evidence"
    assert out.citations == []


def test_answer_with_url_rejected() -> None:
    hit = _hit(WATER)
    for answer in ("Ver https://evil.example", "Pulsa <a href='x'>aquí</a>", "a" * 2001):
        out = validate_answer(_llm((str(hit.chunk_id), "deducible 300 €"), answer=answer), [hit])
        assert out.status == "no_evidence"
        assert out.answer is None


def test_dropped_citation_lowers_confidence() -> None:
    water, fire = _hit(WATER), _hit(FIRE)
    out = validate_answer(
        _llm((str(water.chunk_id), "deducible 300 €"), (str(fire.chunk_id), "inventado del todo")),
        [water, fire],
    )
    assert out.status == "answered"
    assert out.confidence == "low"


def test_extractive_answer_is_degraded_with_valid_citations() -> None:
    hits = [_hit(WATER * 10, page=2), _hit(FIRE, page=3), _hit(WATER, page=1), _hit(FIRE)]
    out = extractive_answer(hits)
    assert out.status == "answered"
    assert out.degraded is True
    assert out.confidence == "low"
    assert out.answer == (
        "No se pudo generar una respuesta en este momento; estos son los fragmentos más "
        "relevantes de la documentación."
    )
    assert [c.chunk_id for c in out.citations] == [h.chunk_id for h in hits[:3]]
    assert [c.page for c in out.citations] == [2, 3, 1]
    for citation, hit in zip(out.citations, hits, strict=False):
        assert citation.fragment == hit.text_redacted[:300]
    # Valid by construction: the validator accepts every extractive fragment.
    llm = _llm(*((str(c.chunk_id), c.fragment) for c in out.citations))
    assert len(validate_answer(llm, hits).citations) == 3
