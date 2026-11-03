import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePath

from fastapi import UploadFile

from app.config import Settings
from app.errors import AppError

CHUNK_SIZE = 1024 * 1024
PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class StoredFile:
    path: Path
    sha256: str
    original_filename: str


def validate_and_store(upload: UploadFile, settings: Settings) -> StoredFile:
    """Stream the upload to `<UPLOAD_DIR>/<uuid>.pdf`, hashing while writing."""
    filename = PurePath(upload.filename or "").name
    if not filename.lower().endswith(".pdf"):
        raise AppError("unsupported_file_type", "Solo se admiten archivos PDF", 415)

    max_bytes = settings.max_upload_mb * 1024 * 1024
    directory = Path(settings.upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4()}.pdf"
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("wb") as out:
            while chunk := upload.file.read(CHUNK_SIZE):
                if size == 0 and not chunk.startswith(PDF_MAGIC):
                    raise AppError("unsupported_file_type", "Solo se admiten archivos PDF", 415)
                size += len(chunk)
                if size > max_bytes:
                    raise AppError(
                        "file_too_large",
                        f"El archivo supera el límite de {settings.max_upload_mb} MB",
                        413,
                    )
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise AppError("unsupported_file_type", "Solo se admiten archivos PDF", 415)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return StoredFile(path, digest.hexdigest(), filename[:255])
