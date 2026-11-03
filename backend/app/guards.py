import re
import unicodedata

from app.errors import AppError

QUESTION_MIN_LEN = 3
QUESTION_MAX_LEN = 1000
ANSWER_MAX_LEN = 2000

_UNTRUSTED_CHARS_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏⁠﻿]")
_DOC_TAG_RE = re.compile(r"<\s*/?\s*doc-", re.IGNORECASE)
# Tight on purpose: flagged chunks are excluded from retrieval, so false positives hide evidence.
_ROLE = (
    r"(?:(?:un|una|an|a|el|la|the)\s+)?"
    r"(?:asistente|assistant|ia|ai|modelo|model|sistema|system|dan)\b"
)
_INJECTION_RE = re.compile(
    r"\b(?:ignora|ignore|olvida|forget|disregard)\b"
    r"(?:\s+(?:todas|todo|las|los|tus|sus|the|all|your|any|previous|prior|anteriores|previas|above))*"
    r"\s+(?:instrucciones|instructions|indicaciones)\b"
    rf"|\b(?:eres ahora|ahora eres|you are now|act as|actua como)\s+{_ROLE}"
    r"|system prompt|instrucciones para el asistente"
    r"|\b(?:responde|contesta|di)\s+siempre\s+que\b"
    r"|<\|im_start\|>|### system"
)
_UNSAFE_ANSWER_RE = re.compile(r"https?://|www\.|<[a-zA-Z/][^>]*>")


def has_control_chars(text: str) -> bool:
    """True if text has control characters other than newline and tab."""
    return any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text)


def sanitize_untrusted(text: str) -> str:
    # Strip invisible chars first so they cannot be used to split a `<doc-` tag.
    text = _UNTRUSTED_CHARS_RE.sub("", text)
    # Repeat until stable: removing one tag must not join its neighbours into a new one.
    while (cleaned := _DOC_TAG_RE.sub("", text)) != text:
        text = cleaned
    return text


def looks_like_injection(text: str) -> bool:
    folded = unicodedata.normalize("NFKD", text).casefold()
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return _INJECTION_RE.search(folded) is not None


def validate_question(text: str) -> str:
    text = text.strip()
    if not QUESTION_MIN_LEN <= len(text) <= QUESTION_MAX_LEN or has_control_chars(text):
        raise AppError(
            "invalid_question", "La pregunta debe tener entre 3 y 1000 caracteres válidos", 422
        )
    return text


def is_safe_answer_text(text: str) -> bool:
    return len(text) <= ANSWER_MAX_LEN and _UNSAFE_ANSWER_RE.search(text) is None
