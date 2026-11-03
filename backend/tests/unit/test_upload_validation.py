import hashlib
import io
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from app.config import Settings
from app.documents.storage import validate_and_store
from app.errors import AppError

PDF = b"%PDF-1.7\n" + b"x" * 1000


def _upload(content: bytes, filename: str = "poliza.pdf") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def _settings(tmp_path: Path, **overrides: int) -> Settings:
    return Settings(upload_dir=str(tmp_path / "uploads"), **overrides)


def test_rejects_non_pdf_magic(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(AppError) as exc:
        validate_and_store(_upload(b"\x89PNG\r\n\x1a\n" + b"0" * 100), settings)

    assert (exc.value.code, exc.value.status_code) == ("unsupported_file_type", 415)
    assert list(Path(settings.upload_dir).iterdir()) == []


def test_rejects_wrong_extension(tmp_path: Path) -> None:
    with pytest.raises(AppError) as exc:
        validate_and_store(_upload(PDF, "poliza.txt"), _settings(tmp_path))

    assert (exc.value.code, exc.value.status_code) == ("unsupported_file_type", 415)


def test_rejects_oversize(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_upload_mb=1)

    with pytest.raises(AppError) as exc:
        validate_and_store(_upload(b"%PDF-" + b"0" * (2 * 1024 * 1024)), settings)

    assert (exc.value.code, exc.value.status_code) == ("file_too_large", 413)
    assert list(Path(settings.upload_dir).iterdir()) == []


def test_hash_matches_content(tmp_path: Path) -> None:
    stored = validate_and_store(_upload(PDF), _settings(tmp_path))

    assert stored.sha256 == hashlib.sha256(PDF).hexdigest()
    assert stored.path.read_bytes() == PDF
    assert stored.path.suffix == ".pdf"
