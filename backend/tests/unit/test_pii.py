import pytest

from app.pii import POLICY_NUMBER_RE, extract_policy_number, redact


@pytest.mark.parametrize(
    ("raw", "placeholder"),
    [
        ("DNI 12345678Z", "DNI [DNI]"),
        ("NIE X1234567L", "NIE [NIE]"),
        ("IBAN ES91 2100 0418 4502 0005 1332", "IBAN [IBAN]"),
        ("IBAN ES9121000418450200051332", "IBAN [IBAN]"),
        ("IBAN ES91-2100-0418-4502-0005-1332", "IBAN [IBAN]"),
        ("iban es91 2100 0418 4502 0005 1332", "iban [IBAN]"),
        ("DNI 12345678z", "DNI [DNI]"),
        ("DNI 12345678-Z", "DNI [DNI]"),
        ("DNI 12345678 Z", "DNI [DNI]"),
        ("DNI 12.345.678-Z", "DNI [DNI]"),
        ("NIE X-1234567-L", "NIE [NIE]"),
        ("NIE x1234567l", "NIE [NIE]"),
        ("DNI 12 345 678 Z", "DNI [DNI]"),
        ("NIE X 1234567 L", "NIE [NIE]"),
        ("escriba a juan.perez@ejemplo.es hoy", "escriba a [EMAIL] hoy"),
        ("tel. +34 612 345 678", "tel. [TELÉFONO]"),
        ("tel. 612345678", "tel. [TELÉFONO]"),
    ],
)
def test_redact_replaces_each_pii_type(raw: str, placeholder: str) -> None:
    assert redact(raw) == placeholder


def test_redact_claim_letter() -> None:
    text = "DNI 12345678Z, móvil +34 612 345 678, IBAN ES91 2100 0418 4502 0005 1332."
    assert redact(text) == "DNI [DNI], móvil [TELÉFONO], IBAN [IBAN]."


@pytest.mark.parametrize(
    "text",
    [
        "Póliza TRM-HOG-2024-00123",
        "Franquicia de 1.500 €",
        "Fecha 15/01/2024",
        "Capital 12.345.678 € a 31/12",
        "Capital de 12.345.678 a todo riesgo",
        # 8 digits + letter is only a DNI when the letter is the control letter (12345678 → Z).
        "Capital 12.345.678 A cargo del asegurado",
        "Ref. 20240115-A",
        # A lowercase one-letter word after a space is prose, even when it is the control letter.
        "límite de 12.000.000 a primer riesgo",
        "NIE X1234567 l",
        "total 98765432 T",
        "NIE X1234567A",
        "Siniestro SIN-2025-0456",
        "Sin datos personales aquí.",
    ],
)
def test_redact_leaves_non_pii_untouched(text: str) -> None:
    assert redact(text) == text


def test_extract_policy_number() -> None:
    assert extract_policy_number("Póliza nº TRM-HOG-2024-00123 vigente") == "TRM-HOG-2024-00123"
    assert extract_policy_number("sin número") is None
    assert POLICY_NUMBER_RE.pattern.startswith(r"\bTRM-")
