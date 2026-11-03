import uuid
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.documents import service
from app.documents.storage import validate_and_store
from app.errors import AppError
from app.models import DocType, DocumentStatus
from app.schemas import DocumentList, DocumentOut

router = APIRouter(prefix="/api/documents", tags=["documents"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", status_code=202)
def upload_document(
    response: Response,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
    doc_type: Annotated[DocType, Form()],
    policy_number: Annotated[str | None, Form(max_length=64)] = None,
    document_date: Annotated[date | None, Form()] = None,
    title: Annotated[str | None, Form(max_length=255)] = None,
) -> DocumentOut:
    stored = validate_and_store(file, settings)
    document, created = service.create_or_get_document(
        session,
        stored,
        doc_type,
        policy_number or None,
        document_date,
        title or Path(stored.original_filename).stem,
    )
    if created:
        service.enqueue_ingestion(session, document)
    else:
        response.status_code = 200
    return service.get_document_out(session, document.id)


@router.get("")
def list_documents(
    session: SessionDep,
    doc_type: DocType | None = None,
    policy_number: str | None = None,
    status: DocumentStatus | None = None,
) -> DocumentList:
    return DocumentList(items=service.list_documents(session, doc_type, policy_number, status))


@router.get("/{document_id}")
def get_document(document_id: uuid.UUID, session: SessionDep) -> DocumentOut:
    return service.get_document_out(session, document_id)


@router.post("/{document_id}/retry")
def retry_document(document_id: uuid.UUID, session: SessionDep) -> DocumentOut:
    service.retry_document(session, document_id)
    return service.get_document_out(session, document_id)


@router.get("/{document_id}/file")
def get_document_file(document_id: uuid.UUID, session: SessionDep) -> FileResponse:
    path = Path(service.get_document(session, document_id).storage_path)
    if not path.is_file():
        raise AppError("file_not_found", "Archivo no disponible", 404)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
    )
