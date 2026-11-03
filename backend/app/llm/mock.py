"""Deterministic offline gateway for tests, CI and the demo (no network)."""

import hashlib
import math
import re
import unicodedata
from typing import TypeVar

from pydantic import BaseModel

from app.llm.gateway import (
    EMBEDDING_DIMENSIONS,
    CompletionMeta,
    ProviderUnavailableError,
    llm_span,
)

T = TypeVar("T", bound=BaseModel)

# shortcut: coupled to the context-block format of the answering prompt; fine for a test-only mock.
_DOC_BLOCK = re.compile(r'<doc-([0-9a-f]+) id="([^"]+)"[^>]*>(.*?)</doc-\1>', re.DOTALL)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_NO_EVIDENCE_ANSWER = "No hay evidencia suficiente en los documentos."
_META = CompletionMeta("mock", "mock", 0, 0, False)


def _tokens(text: str, min_len: int) -> list[str]:
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).casefold()
    return re.findall(rf"\w{{{min_len},}}", folded)


def _embed_one(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token in _tokens(text, 2):
        h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
        vector[h % EMBEDDING_DIMENSIONS] += 1.0 if h >> 63 else -1.0
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class MockGateway:
    embedding_model = "mock"

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    def embed(self, texts: list[str]) -> list[list[float]]:
        with llm_span("llm.embed", "mock") as record:
            self._raise_if_failing()
            record(CompletionMeta("mock", "mock", sum(len(t.split()) for t in texts), 0, False))
            return [_embed_one(t) for t in texts]

    def complete_json(
        self, messages: list[dict[str, str]], schema: type[T]
    ) -> tuple[T, CompletionMeta]:
        with llm_span("llm.completion", "mock") as record:
            self._raise_if_failing()
            prompt = "\n".join(m["content"] for m in messages)
            result = schema.model_validate(self._answer(messages))
            meta = CompletionMeta("mock", "mock", len(prompt.split()), 0, False)
            record(meta)
            return result, meta

    def _raise_if_failing(self) -> None:
        if self._fail:
            raise ProviderUnavailableError("mock provider failure")

    @staticmethod
    def _answer(messages: list[dict[str, str]]) -> dict[str, object]:
        last_user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        question = next(
            (
                line.removeprefix("Pregunta:")
                for line in last_user.splitlines()
                if line.startswith("Pregunta:")
            ),
            "",
        )
        wanted = set(_tokens(question, 4))
        best_id, best_text, best_overlap = "", "", 0
        for _, chunk_id, text in _DOC_BLOCK.findall("\n".join(m["content"] for m in messages)):
            overlap = len(wanted & set(_tokens(text, 4)))
            if overlap > best_overlap:
                best_id, best_text, best_overlap = chunk_id, text, overlap
        if not best_overlap:
            return {
                "answer": _NO_EVIDENCE_ANSWER,
                "citations": [],
                "confidence": "medium",
                "no_evidence": True,
            }
        sentence = _SENTENCE_END.split(best_text.strip(), maxsplit=1)[0][:200]
        return {
            "answer": sentence,
            "citations": [{"chunk_id": best_id, "quote": sentence}],
            "confidence": "medium",
            "no_evidence": False,
        }
