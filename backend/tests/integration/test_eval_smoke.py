import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.mock import MockGateway
from app.models import Document
from eval.run_eval import HERE, reset_eval_documents, run

SAMPLES = Path(__file__).parents[3] / "samples"


@pytest.fixture
def eval_session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
        reset_eval_documents(session)  # the run commits; leave the shared DB as it was found


@pytest.mark.integration
@pytest.mark.eval_smoke
def test_eval_runs_end_to_end_and_reports_every_item(eval_session: Session, tmp_path: Path) -> None:
    settings = get_settings().model_copy(update={"upload_dir": str(tmp_path / "uploads")})
    out_dir = tmp_path / "report"

    report = run(eval_session, MockGateway(), settings, SAMPLES, out_dir, reset=True)

    ids = [i["id"] for i in yaml.safe_load((HERE / "dataset.yaml").read_text(encoding="utf-8"))]
    assert [r["id"] for r in report["items"]] == ids
    markdown = (out_dir / "report.md").read_text(encoding="utf-8")
    assert all(f"| {item_id} |" in markdown for item_id in ids)
    assert "Proveedor: mock (determinista, sin red)" in markdown
    assert (
        json.loads((out_dir / "report.json").read_text(encoding="utf-8"))["meta"]["provider"]
        == "mock"
    )

    # Re-running skips ingestion: same sha256 documents, no duplicates.
    count = eval_session.scalar(select(func.count(Document.id)))
    run(eval_session, MockGateway(), settings, SAMPLES, out_dir)
    assert eval_session.scalar(select(func.count(Document.id))) == count
