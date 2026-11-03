from app.worker import celery_app


@celery_app.task(bind=True)
def ingest_document(self: object, document_id: str) -> None:
    pass  # Task 7 implements ingestion.
