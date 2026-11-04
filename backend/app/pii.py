import re
from collections.abc import Callable

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
# After a space only an uppercase letter counts: "12.000.000 a primer riesgo" is prose.
_LETTER_SEP = r"(?:-?|\s(?=(?-i:[A-Z])))"


def _id_if_valid(placeholder: str) -> Callable[[re.Match[str]], str]:
    # Only a valid control letter makes it an ID: "12.345.678 A cargo de…" is an amount.
    def repl(match: re.Match[str]) -> str:
        *digits, letter = match.groups()
        number = int("".join(digits).translate(str.maketrans("XYZxyz", "012012")))
        return placeholder if _DNI_LETTERS[number % 23] == letter.upper() else match.group(0)

    return repl


# Order matters: IBAN and NIE/DNI must be replaced before the looser phone pattern.
_REDACTIONS: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    (re.compile(r"\bES\d{2}(?:[\s-]?\d{4}){5}\b", re.IGNORECASE), "[IBAN]"),
    (
        re.compile(r"\b([XYZ])[\s-]?(\d{7})" + _LETTER_SEP + r"([A-Z])\b", re.IGNORECASE),
        _id_if_valid("[NIE]"),
    ),
    (
        re.compile(
            r"\b(\d{2})[\s.]?(\d{3})[\s.]?(\d{3})" + _LETTER_SEP + r"([A-Z])\b", re.IGNORECASE
        ),
        _id_if_valid("[DNI]"),
    ),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\+34[\s-]?)?[6789]\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)"), "[TELÉFONO]"),
)

POLICY_NUMBER_RE = re.compile(r"\bTRM-[A-Z]{3}-\d{4}-\d{5}\b")


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def extract_policy_number(text: str) -> str | None:
    match = POLICY_NUMBER_RE.search(text)
    return match.group(0) if match else None
