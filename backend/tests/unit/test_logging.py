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


def test_uvicorn_logs_are_json_without_exception_text(capfd: pytest.CaptureFixture[str]) -> None:
    # uvicorn attaches its own plain-text handlers before the app (and our logging) loads.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addHandler(logging.StreamHandler())
        logging.getLogger(name).propagate = False
    configure_logging("INFO")
    capfd.readouterr()

    logging.getLogger("uvicorn.access").info('"GET /api/documents HTTP/1.1" 200')
    try:
        raise RuntimeError("question text 12345678Z")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")

    out, err = capfd.readouterr()
    lines = [json.loads(line) for line in out.splitlines()]
    assert [r["logger"] for r in lines] == ["uvicorn.access", "uvicorn.error"]
    assert lines[1]["exc_type"] == "RuntimeError"
    assert "12345678Z" not in out + err
