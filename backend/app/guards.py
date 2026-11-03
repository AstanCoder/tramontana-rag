import re
import unicodedata

from app.errors import AppError

QUESTION_MIN_LEN = 3
QUESTION_MAX_LEN = 1000
ANSWER_MAX_LEN = 2000

_UNTRUSTED_CHARS_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏⁠﻿]")
_DOC_TAG_RE = re.compile(r"</?doc-")
_INJECTION_RE = re.compile(
    r"(?:ignor\w*|olvid\w*|forget|disregard)\W+(?:\w+\W+){0,4}?(?:instrucciones|instructions)"
    r"|eres ahora|you are now"
    r"|system prompt|instrucciones para el asistente"
    r"|responde (?:siempre )?que"
    r"|<\|im_start\|>|### system"
    r"|\bact as\b|\bactua como\b"
)
_UNSAFE_ANSWER_RE = re.compile(r"https?://|www\.|<[a-zA-Z/][^>]*>")


def has_control_chars(text: str) -> bool:
    """True if text has control characters other than newline and tab."""
    return any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text)


def sanitize_untrusted(text: str) -> str:
    # Strip invisible chars first so they cannot be used to split a `<doc-` tag.
    return _DOC_TAG_RE.sub("", _UNTRUSTED_CHARS_RE.sub("", text))


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
