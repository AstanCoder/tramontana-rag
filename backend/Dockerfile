FROM python:3.12.16-slim-bookworm

# tesseract: Docling OCR for scanned PDFs; libgl1/libglib2.0-0: required by opencv (Docling).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data/uploads /opt/models \
    && chown app:app /data/uploads

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/models/hf DOCLING_ARTIFACTS_PATH=/opt/models/docling

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Models are baked in so the runtime never needs the network (or HF) to parse and chunk.
RUN docling-tools models download layout tableformer -o /opt/models/docling \
    && python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('sentence-transformers/all-MiniLM-L6-v2')"
ENV HF_HUB_OFFLINE=1

COPY alembic.ini docker-entrypoint.sh ./
COPY alembic ./alembic
COPY app ./app
COPY eval ./eval

USER app
EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
