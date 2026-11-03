from dataclasses import dataclass
from functools import lru_cache
from typing import cast

from docling_core.transforms.chunker.doc_chunk import DocChunk
from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer,
    ChunkingSerializerProvider,
)
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.transforms.serializer.base import BaseDocSerializer
from docling_core.transforms.serializer.markdown import MarkdownTableSerializer
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from app.models import ChunkKind

TOKENIZER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MAX_TOKENS = 512


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    kind: ChunkKind
    text: str
    embed_text: str
    headings: list[str]
    page_start: int
    page_end: int


class _MarkdownTablesProvider(ChunkingSerializerProvider):
    # The chunking default serializes tables as triplets; markdown keeps rows readable for the LLM.
    def get_serializer(self, doc: DoclingDocument) -> BaseDocSerializer:
        return ChunkingDocSerializer(doc=doc, table_serializer=MarkdownTableSerializer())


@lru_cache
def _chunker() -> HybridChunker:
    return HybridChunker(
        tokenizer=HuggingFaceTokenizer.from_pretrained(TOKENIZER_MODEL, max_tokens=MAX_TOKENS),
        serializer_provider=_MarkdownTablesProvider(),
        repeat_table_header=True,
    )


def chunk_document(doc: DoclingDocument) -> list[ChunkDraft]:
    chunker = _chunker()
    drafts = []
    for ordinal, base_chunk in enumerate(chunker.chunk(doc)):
        chunk = cast(DocChunk, base_chunk)  # HybridChunker always yields DocChunk
        items = chunk.meta.doc_items
        pages = [prov.page_no for item in items for prov in item.prov]
        is_table = any(item.label == DocItemLabel.TABLE for item in items)
        drafts.append(
            ChunkDraft(
                ordinal=ordinal,
                kind=ChunkKind.table if is_table else ChunkKind.text,
                text=chunk.text,
                embed_text=chunker.contextualize(chunk),
                headings=list(chunk.meta.headings or []),
                page_start=min(pages),
                page_end=max(pages),
            )
        )
    return drafts
