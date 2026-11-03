from typing import Any

from celery import Celery
from celery.signals import setup_logging, worker_process_init

from app.config import get_settings
from app.logging import configure_logging
from app.telemetry import setup_telemetry

settings = get_settings()

celery_app = Celery(
    "tramontana",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.ingestion.tasks", "app.answering.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)


@setup_logging.connect
def _configure_logging(**_: Any) -> None:
    # Connecting this signal stops Celery from replacing our JSON logging and redirecting stdout.
    configure_logging(settings.log_level)


@worker_process_init.connect
def _init_telemetry(**_: Any) -> None:
    # Per child process: the span exporter's background thread does not survive fork.
    setup_telemetry("tramontana-worker")
