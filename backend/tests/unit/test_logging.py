import json
import logging

import pytest

from app.logging import configure_logging


def test_stdlib_logs_are_json_and_content_loggers_only_warn(
    capfd: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    capfd.readouterr()

    logging.getLogger("LiteLLM").info("prompt text")
    logging.getLogger("docling.document_converter").info("file path")
    logging.getLogger("LiteLLM").warning("rate limited")
    logging.getLogger("celery.worker").info("worker ready")

    lines = [json.loads(line) for line in capfd.readouterr().out.splitlines()]
    assert [(r["logger"], r["event"]) for r in lines] == [
        ("LiteLLM", "rate limited"),
        ("celery.worker", "worker ready"),
    ]
