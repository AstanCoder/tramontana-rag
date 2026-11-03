from functools import lru_cache
from pathlib import Path

from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.document import DoclingDocument
from pypdf import PdfReader


class CorruptDocumentError(Exception):
    """The PDF cannot be opened: malformed, encrypted, empty or over the page limit."""


class UnreadableDocumentError(Exception):
    """The PDF opens but yields no text, even after OCR."""


@lru_cache
def _converter() -> DocumentConverter:
    options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
        ocr_options=TesseractCliOcrOptions(lang=["spa"]),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def _page_count(path: Path) -> int:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise CorruptDocumentError("encrypted pdf")
        return len(reader.pages)
    except (CorruptDocumentError, OSError):
        raise
    except Exception as exc:  # pypdf raises many types on malformed input
        raise CorruptDocumentError(type(exc).__name__) from exc


def parse_pdf(path: Path, max_pages: int) -> DoclingDocument:
    pages = _page_count(path)
    if not 0 < pages <= max_pages:
        raise CorruptDocumentError(f"page count {pages} outside 1..{max_pages}")
    result = _converter().convert(path, raises_on_error=False)
    if result.status != ConversionStatus.SUCCESS:
        raise CorruptDocumentError(f"conversion status {result.status.value}")
    doc = result.document
    if not any(item.text.strip() for item in doc.texts) and not doc.tables:
        raise UnreadableDocumentError("no text after OCR")
    return doc
