import re

# Order matters: IBAN and NIE/DNI must be replaced before the looser phone pattern.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bES\d{2}(?:\s?\d{4}){5}\b"), "[IBAN]"),
    (re.compile(r"\b[XYZ]\d{7}[A-HJ-NP-TV-Z]\b"), "[NIE]"),
    (re.compile(r"\b\d{8}[A-HJ-NP-TV-Z]\b"), "[DNI]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\+34[\s-]?)?[6789]\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)"), "[TELÉFONO]"),
)

POLICY_NUMBER_RE = re.compile(r"\bTRM-[A-Z]{3}-\d{4}-\d{5}\b")


def redact(text: str) -> str:
    for pattern, placeholder in _REDACTIONS:
        text = pattern.sub(placeholder, text)
    return text


def extract_policy_number(text: str) -> str | None:
    match = POLICY_NUMBER_RE.search(text)
    return match.group(0) if match else None
