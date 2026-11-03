from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.ingestion import chunking
from app.ingestion.chunking import ChunkDraft, chunk_document
from app.ingestion.parsing import (
    CorruptDocumentError,
    TooManyPagesError,
    UnreadableDocumentError,
    parse_pdf,
)
from app.models import ChunkKind

SAMPLES = Path(__file__).parents[3] / "samples"
HOME_POLICY = SAMPLES / "poliza-hogar-TRM-HOG-2024-00123.pdf"
MAX_PAGES = 200


@pytest.fixture(scope="module")
def home_chunks() -> list[ChunkDraft]:
    return chunk_document(parse_pdf(HOME_POLICY, MAX_PAGES))


def test_table_chunk_is_markdown_with_header(home_chunks: list[ChunkDraft]) -> None:
    tables = [c for c in home_chunks if c.kind == ChunkKind.table]
    assert tables
    table = tables[0]
    assert "| Cobertura " in table.text
    assert "Deducible" in table.text
    assert "Daños por agua" in table.text
    assert table.page_start == 2


def test_pages_preserved(home_chunks: list[ChunkDraft]) -> None:
    clause = next(c for c in home_chunks if "sótanos" in c.text)
    assert (clause.page_start, clause.page_end) == (3, 3)
    assert home_chunks[0].page_start == 1
    assert [c.ordinal for c in home_chunks] == list(range(len(home_chunks)))


def test_headings_in_embed_text(home_chunks: list[ChunkDraft]) -> None:
    clause = next(c for c in home_chunks if "sótanos" in c.text)
    assert clause.headings
    assert clause.headings[-1] in clause.embed_text
    assert clause.text in clause.embed_text


def test_no_chunk_exceeds_512_tokens(home_chunks: list[ChunkDraft]) -> None:
    tokenizer = chunking._chunker().tokenizer
    assert all(tokenizer.count_tokens(c.embed_text) <= 512 for c in home_chunks)


def test_scanned_pdf_is_ocrd() -> None:
    doc = parse_pdf(SAMPLES / "poliza-auto-escaneada-TRM-AUT-2024-00777.pdf", MAX_PAGES)
    texts = [c.embed_text for c in chunk_document(doc)]
    # OCR output is noisy ("nº" reads as "n*"), so match on robust tokens only.
    assert any("TRM-AUT-2024-00777" in t for t in texts)
    assert any("Responsabilidad civil" in t for t in texts)


def test_corrupt_raises() -> None:
    with pytest.raises(CorruptDocumentError):
        parse_pdf(SAMPLES / "corrupto.pdf", MAX_PAGES)


def test_encrypted_pdf_raises(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=SAMPLES / "carta-siniestro-SIN-2025-0456.pdf")
    writer.encrypt("secret")
    encrypted = tmp_path / "encrypted.pdf"
    writer.write(encrypted)
    with pytest.raises(CorruptDocumentError):
        parse_pdf(encrypted, MAX_PAGES)


def test_too_many_pages_raises() -> None:
    with pytest.raises(TooManyPagesError):
        parse_pdf(HOME_POLICY, max_pages=2)


def test_blank_pdf_is_unreadable(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    blank = tmp_path / "blank.pdf"
    writer.write(blank)
    with pytest.raises(UnreadableDocumentError):
        parse_pdf(blank, MAX_PAGES)
