"""Evaluation harness: ingest the sample PDFs, answer every dataset item, report metrics.

Run inside the stack (GIT_SHA is stamped on the report):
    docker compose run --rm -e GIT_SHA=$(git rev-parse --short HEAD) api \
        python -m eval.run_eval --reset
"""

import argparse
import hashlib
import json
import os
import unicodedata
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.answering.citations import normalize_for_match
from app.answering.service import answer
from app.config import Settings, get_settings
from app.db import session_scope
from app.documents.service import create_or_get_document
from app.documents.storage import validate_and_store
from app.ingestion.service import run_ingestion
from app.llm.gateway import Gateway, get_gateway
from app.models import DocType, Document, DocumentStatus
from app.retrieval.search import RetrievedChunk
from app.schemas import AnswerOut, QuestionFilters

K = 6  # chunks handed to the LLM (retrieval top_n)
EVAL_TASK_PREFIX = "eval-"  # marks documents ingested by this script so --reset can find them
SKIPPED_SAMPLES = {"corrupto.pdf"}  # failure demo, not a knowledge source
HERE = Path(__file__).parent


class Expected(BaseModel):
    status: Literal["answered", "no_evidence"]
    document: str | None
    page: int | None
    keywords: list[str]
    forbidden: list[str] = Field(default_factory=list)
    answer: str | None = None  # documentation only


class EvalItem(BaseModel):
    id: str
    question: str
    filters: QuestionFilters = Field(default_factory=QuestionFilters)
    expected: Expected


# --- Metrics (pure) -----------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Case-, accent- and whitespace-insensitive form."""
    decomposed = unicodedata.normalize("NFKD", text)
    return " ".join(
        "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().split()
    )


def _matches(hit: RetrievedChunk, expected_doc: str, expected_page: int | None) -> bool:
    return hit.document_title == expected_doc and (
        expected_page is None or hit.page_start <= expected_page <= hit.page_end
    )


def reciprocal_rank(
    hits: list[RetrievedChunk], expected_doc: str, expected_page: int | None, k: int
) -> float:
    for rank, hit in enumerate(hits[:k], start=1):
        if _matches(hit, expected_doc, expected_page):
            return 1.0 / rank
    return 0.0


def hit_at_k(
    hits: list[RetrievedChunk], expected_doc: str, expected_page: int | None, k: int
) -> bool:
    return reciprocal_rank(hits, expected_doc, expected_page, k) > 0.0


def citation_faithfulness(answer_out: AnswerOut, hits: list[RetrievedChunk]) -> float:
    """Share of citations whose fragment is in the cited retrieved chunk and whose page matches."""
    if not answer_out.citations:
        return 0.0
    by_id = {h.chunk_id: h for h in hits}
    faithful = 0
    for c in answer_out.citations:
        hit = by_id.get(c.chunk_id)
        faithful += (
            hit is not None
            and c.page == hit.page_start
            and normalize_for_match(c.fragment) in normalize_for_match(hit.text_redacted)
        )
    return faithful / len(answer_out.citations)


def keyword_recall(answer_text: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    folded = _fold(answer_text)
    return sum(_fold(k) in folded for k in keywords) / len(keywords)


def refusal_precision_recall(pairs: list[tuple[str, str]]) -> tuple[float, float]:
    """`pairs` are (expected_status, actual_status); a refusal is `no_evidence`. 0/0 counts as 1."""
    refused = [expected for expected, actual in pairs if actual == "no_evidence"]
    should_refuse = [actual for expected, actual in pairs if expected == "no_evidence"]
    correct = sum(e == "no_evidence" for e in refused)
    return correct / len(refused) if refused else 1.0, (
        correct / len(should_refuse) if should_refuse else 1.0
    )


# --- Ingestion ----------------------------------------------------------------------------------


def reset_eval_documents(session: Session) -> None:
    docs = session.scalars(
        select(Document).where(Document.ingest_task_id.like(f"{EVAL_TASK_PREFIX}%"))
    ).all()
    for doc in docs:
        Path(doc.storage_path).unlink(missing_ok=True)
    session.execute(delete(Document).where(Document.id.in_([d.id for d in docs])))
    session.commit()


def ingest_samples(
    session: Session, gateway: Gateway, settings: Settings, samples_dir: Path
) -> dict[str, str]:
    """Ingest the manifest's PDFs; file -> stored title.

    A sample already ready (by sha256) is skipped unless another embedding model indexed it.
    """
    manifest = yaml.safe_load((samples_dir / "manifest.yaml").read_text(encoding="utf-8"))
    titles: dict[str, str] = {}
    for entry in manifest:
        if entry["file"] in SKIPPED_SAMPLES:
            continue
        path = samples_dir / entry["file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        document = session.scalars(select(Document).where(Document.sha256 == digest)).one_or_none()
        if document is None:
            with path.open("rb") as handle:
                stored = validate_and_store(UploadFile(handle, filename=path.name), settings)
            document, _ = create_or_get_document(
                session,
                stored,
                DocType(entry["doc_type"]),
                entry["policy_number"],
                date.fromisoformat(entry["document_date"]) if entry["document_date"] else None,
                entry["title"],
            )
        titles[entry["file"]] = document.title
        if (
            document.status == DocumentStatus.ready
            and document.embedding_model == gateway.embedding_model
        ):
            continue
        task_id = f"{EVAL_TASK_PREFIX}{uuid.uuid4()}"
        document.status = DocumentStatus.pending
        document.error_code = None
        document.ingest_task_id = task_id
        session.commit()
        run_ingestion(session, document.id, task_id, gateway)
    return titles


# --- Evaluation ---------------------------------------------------------------------------------


def evaluate_item(
    session: Session, gateway: Gateway, item: EvalItem, titles: dict[str, str]
) -> dict[str, Any]:
    out, hits, meta = answer(session, gateway, item.question, item.filters)
    exp = item.expected
    text = out.answer or ""
    row: dict[str, Any] = {
        "id": item.id,
        "question": item.question,
        "expected_status": exp.status,
        "expected_document": exp.document,
        "expected_page": exp.page,
        "status": out.status,
        "degraded": out.degraded,
        "answer": text,
        "citations": [{"document": c.document_title, "page": c.page} for c in out.citations],
        "hit_at_k": None,
        "reciprocal_rank": None,
        "citation_faithfulness": None,
        "keyword_recall": None,
        "injection_resisted": None,
        "provider": meta.provider if meta else None,
        "model": meta.model if meta else None,
    }
    if exp.document is not None:
        title = titles[exp.document]
        row["hit_at_k"] = hit_at_k(hits, title, exp.page, K)
        row["reciprocal_rank"] = reciprocal_rank(hits, title, exp.page, K)
    if out.status == "answered":
        row["citation_faithfulness"] = citation_faithfulness(out, hits)
    if exp.status == "answered":
        row["keyword_recall"] = keyword_recall(text, exp.keywords)
    if exp.forbidden:
        # A refusal says nothing about resisting the injection: only answered items are scored.
        row["injection_resisted"] = (
            not any(_fold(f) in _fold(text) for f in exp.forbidden)
            if out.status == "answered"
            else "n/a"
        )
    return row


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def column(name: str) -> list[Any]:
        return [r[name] for r in rows if r[name] is not None]

    precision, recall = refusal_precision_recall(
        [(r["expected_status"], r["status"]) for r in rows]
    )
    injection = [v for v in column("injection_resisted") if isinstance(v, bool)]
    return {
        f"hit@{K}": _mean([float(v) for v in column("hit_at_k")]),
        "mrr": _mean(column("reciprocal_rank")),
        "citation_faithfulness": _mean(column("citation_faithfulness")),
        "keyword_recall": _mean(column("keyword_recall")),
        "refusal_precision": precision,
        "refusal_recall": recall,
        "injection_resisted": f"{sum(injection)}/{len(injection)}" if injection else "n/a",
        # Extractive fallback after a provider failure: those rows measure the outage, not quality.
        "degraded": sum(bool(r["degraded"]) for r in rows),
    }


# --- Reporting ----------------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    meta, agg = report["meta"], report["aggregate"]
    lines = ["# Informe de evaluación", ""]
    if meta["provider"] == "mock":
        lines += [
            "> **Proveedor: mock (determinista, sin red) — no refleja la calidad de Gemini.** "
            "Las métricas de esta ejecución salen del embedding y el generador léxicos de "
            "pruebas; sirven para verificar el arnés, no para valorar el sistema real.",
            "",
        ]
    lines += [
        f"- Proveedor: `{meta['provider']}`",
        f"- Modelo: `{meta['model']}`",
        f"- Fecha: {meta['date']}",
        f"- Commit base: `{meta['git_sha']}`",
        f"- Elementos: {len(report['items'])}",
        "",
        "## Agregado",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        *(f"| {name} | {_fmt(value)} |" for name, value in agg.items()),
        "",
        "## Por elemento",
        "",
        f"| Id | Pregunta | Esperado | Obtenido | hit@{K} | RR | Fidelidad | Keywords |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["items"]:
        expected = (
            f"{r['expected_document']} p.{_fmt(r['expected_page'])}"
            if r["expected_document"]
            else r["expected_status"]
        )
        obtained = r["status"] + (f" ({len(r['citations'])} citas)" if r["citations"] else "")
        if r["degraded"]:
            obtained += "; degradada"
        if r["injection_resisted"] is not None:
            obtained += f"; inyección resistida: {_fmt(r['injection_resisted'])}"
        lines.append(
            f"| {r['id']} | {r['question']} | {expected} | {obtained} | {_fmt(r['hit_at_k'])} "
            f"| {_fmt(r['reciprocal_rank'])} | {_fmt(r['citation_faithfulness'])} "
            f"| {_fmt(r['keyword_recall'])} |"
        )
    return "\n".join(lines) + "\n"


def run(
    session: Session,
    gateway: Gateway,
    settings: Settings,
    samples_dir: Path,
    out_dir: Path,
    *,
    reset: bool = False,
) -> dict[str, Any]:
    items = [
        EvalItem.model_validate(raw)
        for raw in yaml.safe_load((HERE / "dataset.yaml").read_text(encoding="utf-8"))
    ]
    if reset:
        reset_eval_documents(session)
    titles = ingest_samples(session, gateway, settings, samples_dir)
    rows = [evaluate_item(session, gateway, item, titles) for item in items]
    first = next((r for r in rows if r["provider"]), None)
    report = {
        "meta": {
            "provider": first["provider"] if first else settings.llm_provider,
            "model": first["model"] if first else settings.llm_model,
            "date": datetime.now(UTC).date().isoformat(),
            "git_sha": os.environ.get("GIT_SHA", "desconocido"),
        },
        "aggregate": aggregate(rows),
        "items": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown = render_markdown(report)
    (out_dir / "report.md").write_text(markdown, encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="delete earlier eval-ingested documents"
    )
    parser.add_argument("--samples-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=HERE)
    args = parser.parse_args()
    # The compose stack mounts the samples at /samples; locally they live at the repo root.
    samples_dir = args.samples_dir or (
        Path("/samples") if Path("/samples").is_dir() else HERE.parents[1] / "samples"
    )
    with session_scope() as session:
        report = run(
            session, get_gateway(), get_settings(), samples_dir, args.out_dir, reset=args.reset
        )
    print(render_markdown(report))


if __name__ == "__main__":
    main()
