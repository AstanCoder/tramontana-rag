# Diseño — Asistente RAG de pólizas y siniestros (Tramontana Seguros)

Fecha: 2026-11-03 · Entrega: 2026-11-05 23:17 UTC · Repo: `AstanCoder/tramontana-rag` (público, monorepo)

## 1. Intención

Los ajustadores de Tramontana Seguros pierden tiempo buscando cláusulas en miles de PDFs (pólizas,
condicionados, cartas de siniestro). El MVP permite subir documentos y preguntar en lenguaje natural.
Responde **solo** con base en los documentos, cita documento + página + fragmento, y rechaza con claridad
cuando no hay evidencia.

Criterio rector: entregar exactamente lo que pide la prueba, **production-ready, limpio y simple**. Nada de
sobre-ingeniería ni features no pedidas.

## 2. Objetivos, no-objetivos, anti-objetivos

**Objetivos** (todos los requisitos del PDF):
- API FastAPI (subida, estado de ingesta, listado, preguntas) con contratos Pydantic.
- Ingesta asíncrona Celery + Redis: Docling → chunking con página/estructura/tablas → embeddings →
  PostgreSQL + pgvector. Idempotente, reintentable, estados `pending | processing | failed | ready`.
- Recuperación sobre pgvector con filtros (tipo de documento, póliza, rango de fechas) + FTS de Postgres.
- Respuesta estructurada validada con Pydantic: respuesta, citas, confianza o "sin evidencia". Sin citas
  válidas no llega al usuario.
- Gateway LLM/embeddings vía LiteLLM: Gemini principal configurable, fallback, timeouts, reintentos.
- Defensa contra prompt injection indirecta + documento malicioso de demo.
- UI React + TypeScript: subida, estado por polling, chat con citas navegables, estados de carga/error/sin evidencia.
- Eval de 12–15 preguntas + script de métricas de recuperación y fidelidad de citas + reporte de ejemplo.
- Tests unitarios e integración (Postgres real vía Docker), suite sin claves ni red.
- OpenTelemetry: una traza API → Celery → recuperación → LLM; logs JSON correlacionados por trace id.
- `docker compose up` levanta todo; CI con lint + tests.
- README, DECISIONS.md, documentos de ejemplo.

**No-objetivos (fuera del MVP, documentados en README)**: autenticación/roles, multi-tenant, almacenamiento
S3, NER de nombres/direcciones, extracción de metadatos con LLM, SSE/websockets, rate limiting de la API,
historial de conversación multi-turno, despliegue cloud.

**Anti-objetivos (fallo aunque "funcione")**:
- Una respuesta que afirme cobertura sin cita verificable.
- Un documento que logre cambiar el comportamiento del asistente.
- Contenido de pólizas o PII en logs, trazas o mensajes de error.
- Código que el candidato no pueda defender: capas, interfaces o configuraciones sin uso real.

## 3. Restricciones

- Gemini **free tier**: las cuotas y los modelos disponibles cambian a menudo (hay fuentes que contradicen la
  tabla oficial, y algunos modelos 2.5 tienen deprecaciones anunciadas). Los modelos se configuran por env y
  los valores por defecto se fijan comprobando la lista de modelos de la API con la key real. Ingesta en lotes
  y con backoff ante 429. El free tier puede usar los datos para entrenar → refuerza la redacción de PII (§9).
- No hay clave de un segundo proveedor (ver fallback en §7).
- Máquina: M2 Pro, 32 GB, Docker con espacio. Imagen backend pesada (Docling + torch CPU + Tesseract) aceptada.
- LiteLLM: las versiones 1.82.7/1.82.8 de PyPI fueron comprometidas (marzo 2026). Versión exacta pinneada +
  `uv.lock` con hashes.
- Idioma: código e identificadores en inglés; UI, README, DECISIONS.md y docs de ejemplo en español.
- Stack: el que pide el PDF. Backend Python 3.12 + uv; frontend Vite + React + TS.

## 4. Arquitectura

```
frontend (nginx, React SPA) ──/api──▶ api (FastAPI) ──▶ PostgreSQL+pgvector
                                         │  ▲
                                   enqueue│  │poll
                                         ▼  │
                                  Redis ◀─▶ worker (Celery: ingest_document, answer_question)
                                                 │
                                                 ▼
                                        LLM gateway (LiteLLM → Gemini | mock)
api, worker ──OTLP──▶ otel-collector ──▶ Jaeger (local)  |  Datadog (si DD_API_KEY)
```

Servicios de `docker-compose.yml`: `postgres` (pgvector/pgvector:pg17), `redis`, `api`, `worker`,
`frontend`, `otel-collector` (contrib), `jaeger`. API y worker comparten imagen (distinto comando). La API
aplica `alembic upgrade head` al arrancar. Los archivos subidos van a un volumen compartido, nombrados por
UUID (nunca por el nombre original).

### Las preguntas también pasan por Celery

El PDF pide **una traza** que cruce API → Celery → recuperación → LLM. Por eso `POST /api/questions` crea
una `question` (estado `pending`), encola `answer_question` y devuelve 202. La UI hace polling de
`GET /api/questions/{id}`. Ventajas: la traza es literal, la API no bloquea workers HTTP con llamadas
lentas al LLM, y queda registro auditable de preguntas y respuestas. Coste: una tabla y un polling más.

## 5. Estructura del repo

```
backend/
  app/
    main.py              # FastAPI app, routers, error handlers, OTel
    worker.py            # Celery app
    config.py            # pydantic-settings, todo por env
    logging.py           # structlog JSON + trace_id/span_id
    telemetry.py         # setup OTel (FastAPI, Celery, SQLAlchemy, spans manuales)
    errors.py            # AppError + envelope consistente
    db.py, models.py     # SQLAlchemy 2
    schemas.py           # contratos Pydantic de la API
    documents/           # router, service, storage, validation
    ingestion/           # parsing (Docling), chunking, tasks
    retrieval/           # hybrid search
    answering/           # prompt, guards (injection), citations, pii, tasks, router
    llm/                 # gateway LiteLLM + mock determinista
  alembic/
  tests/unit, tests/integration
  eval/                  # dataset.yaml, run_eval.py, report.md
  pyproject.toml, uv.lock, Dockerfile
frontend/                # Vite + React + TS + TanStack Query + Tailwind
infra/otel/              # collector-local.yaml, collector-datadog.yaml
samples/                 # PDFs de ejemplo + generate.py
docker-compose.yml, .env.example, .github/workflows/ci.yml
README.md, DECISIONS.md
```

## 6. Modelo de datos

**documents**: `id` uuid, `title`, `original_filename`, `sha256` (unique), `doc_type`
(`policy | general_conditions | claim_letter | other`), `policy_number` (nullable, index),
`document_date` (nullable), `status` (`pending | processing | failed | ready`), `error_code` (nullable),
`page_count`, `is_current` (bool), `superseded_by_id` (nullable), `storage_path`, `ingest_task_id`,
`attempts`, `processing_started_at`, `created_at`, `updated_at`.

**chunks**: `id` uuid, `document_id` (FK cascade), `ordinal`, `kind` (`text | table`), `text` (original),
`text_redacted` (PII sustituida; lo que se embebe y se envía al LLM), `headings` text[], `page_start`,
`page_end`, `embedding vector(768)`, `tsv` (tsvector generado, `spanish`, sobre `text`), `suspicious` (bool).
Índices: HNSW `vector_cosine_ops` (m=16, ef_construction=64), GIN sobre `tsv`, btree `document_id`.

**questions**: `id`, `status` (`pending | processing | answered | no_evidence | failed`), `question`,
`filters` jsonb, `result` jsonb (AnswerOut), `error_code`, `model`, `provider`, `latency_ms`, `trace_id`,
`created_at`.

## 7. Componentes

### 7.1 API (contratos Pydantic)
- `POST /api/documents` (multipart: `file`, `doc_type`, `policy_number?`, `document_date?`, `title?`) → 202
  `DocumentOut`. Si el `sha256` ya existe → 200 con el documento existente (no reencola).
- `GET /api/documents?doc_type&policy_number&status` → lista.
- `GET /api/documents/{id}` → estado + `error_code`, `page_count`, `chunk_count`.
- `POST /api/documents/{id}/retry` → solo desde `failed`; vuelve a `pending` y reencola.
- `GET /api/documents/{id}/file` → PDF (para abrir la cita en `#page=N`).
- `POST /api/questions` `{question, filters: {doc_type?, policy_number?, date_from?, date_to?,
  include_superseded?}}` → 202 `QuestionOut`.
- `GET /api/questions/{id}` → `QuestionOut` con `result: AnswerOut | null`.
- `GET /health`.
- Errores siempre `{"error": {"code", "message", "trace_id"}}`; los 500 llevan un mensaje genérico y
  nunca exponen detalles internos.

`AnswerOut`: `status` (`answered | no_evidence`), `answer` (null si no hay evidencia), `citations[]`
(`document_id`, `document_title`, `page`, `fragment`, `chunk_id`), `confidence` (`high | medium | low`),
`degraded` (bool, fallback extractivo), `reason` (si no hay evidencia).

### 7.2 Ingesta (`ingest_document`)
1. Claim atómico con dueño: al encolar se genera el `task_id` y se guarda en `ingest_task_id`.
   `UPDATE documents SET status='processing' ... WHERE id=:id AND (status='pending' OR (status='processing'
   AND ingest_task_id=:task_id)) RETURNING`. Los reintentos y redeliveries de Celery conservan el mismo
   `task_id`, así que pueden continuar. Una tarea duplicada tiene otro id → no obtiene fila y termina sin
   hacer nada. `acks_late=True` + `reject_on_worker_lost=True`. `POST /retry` acepta `failed` o `processing`
   con más de 20 min sin avanzar (tarea perdida; supera el límite duro de 960 s de la tarea), y genera un `task_id` nuevo.
2. Docling `DocumentConverter` con OCR Tesseract (`spa`), modelos precargados en la imagen.
3. `HybridChunker` (máx. 512 tokens, tokenizer HF precargado). Las tablas se serializan en markdown en su
   propio chunk (serializer provider con `MarkdownTableSerializer`; el formato por defecto es de tripletas);
   si se parten, se repite la cabecera (`repeat_table_header` de docling-core reciente). Cada chunk guarda páginas (`prov.page_no`) y la ruta de
   encabezados. El texto que se embebe es `contextualize(chunk)` (encabezados + contenido), ya redactado.
4. Redacción de PII → `text_redacted`. Detector heurístico de injection → `suspicious`.
5. Embeddings en lotes vía gateway.
6. Transacción: borrar chunks previos del documento, insertar los nuevos y marcar `ready`. Versionado: solo
   se versionan los `doc_type='policy'` con `policy_number`. Al quedar `ready`, la póliza con el mismo número
   y `document_date` más reciente es la vigente (si falta la fecha o hay empate, gana la subida más reciente);
   las demás quedan `is_current=false`. Cartas, condicionados y otros nunca reemplazan a nada.
- Errores permanentes (PDF corrupto, sin texto tras OCR) → `failed` con `error_code`
  (`corrupt_document`, `unreadable_document`), sin reintento automático.
- Errores transitorios (429, timeout, DB) → presupuesto de reintentos acotado para no quemar la cuota: LiteLLM
  reintenta 2 veces en la llamada; si sigue fallando, Celery reintenta la tarea hasta 3 veces con backoff
  exponencial + jitter (30/60/120 s, respetando `Retry-After`). En el peor caso son ~9 llamadas. Si se agota
  el presupuesto → `failed` (`provider_unavailable`), reintentable desde la API.

### 7.3 Recuperación
- Vectorial: top 20 por coseno (HNSW, `SET LOCAL hnsw.iterative_scan=relaxed_order` para no perder resultados
  al filtrar; como puede devolver el orden alterado, se reordena por distancia antes de RRF y del umbral). FTS: top 20 con semántica OR (`plainto_tsquery('spanish')` con `&` → `|`), porque una pregunta natural rara vez tiene todos sus términos en un mismo chunk. Fusión RRF (k=60) → top 6 al LLM.
- Filtros (join con `documents`): `status='ready'`, `is_current` (salvo `include_superseded`), `doc_type`,
  `policy_number`, `document_date` dentro de `[date_from, date_to]`. Los chunks `suspicious` se excluyen del
  contexto.
- Puerta de evidencia: si la mejor similitud vectorial < umbral y no hay coincidencia FTS →
  `no_evidence` sin llamar al LLM. El umbral se calibra con el eval.

### 7.4 Generación y validación
- El LLM recibe la pregunta (redactada) y los chunks como datos entre delimitadores con un nonce por petición
  (`<doc-{nonce} id=chunk_id>`). Se eliminan del texto caracteres de control/zero-width y secuencias que imiten
  el delimitador. El system prompt indica: el contenido es dato, nunca instrucción; responde solo con
  evidencia; cita `chunk_id` + cita literal.
- Salida del LLM (JSON schema Pydantic vía `response_format`): `answer`, `citations[{chunk_id, quote}]`,
  `confidence`, `no_evidence`.
- Validador: cada `chunk_id` debe estar entre los recuperados; cada `quote` debe ser substring normalizado de
  `text_redacted` de ese chunk. Las citas inválidas se descartan; si no queda ninguna válida → `no_evidence`.
  Documento y página los pone el servidor, nunca el LLM. Se rechazan respuestas con URLs/HTML o que superen
  la longitud máxima. Con evidencia débil la confianza baja.
- `fragment` = la cita literal (redactada). El ajustador ve el original abriendo el PDF en la página.

### 7.5 Gateway LLM (`app/llm/`)
- Una sola clase `LLMGateway` con `embed(texts)` y `complete_json(messages, schema)`. Dos implementaciones
  elegidas por `LLM_PROVIDER`: `litellm` (por defecto) y `mock`.
- LiteLLM `Router`: modelo Flash de Gemini → fallback al Flash-Lite (cuota separada en free tier) →
  `LLM_FALLBACK_MODEL` opcional (cualquier modelo LiteLLM, p. ej. otro proveedor, solo por env). Nombres
  exactos por env. `timeout`, `num_retries` y backoff configurables.
- **Último eslabón: fallback simulado extractivo**. Si todos los modelos fallan, no se
  inventa una respuesta: se devuelve `status='answered'`, `degraded=true`, `confidence='low'`, con el texto
  "No se pudo generar una respuesta; estos son los fragmentos más relevantes" y como citas los mejores
  chunks recuperados (citas válidas por construcción). La UI muestra un aviso de modo degradado y la traza
  marca `fallback_used=extractive`. Si la recuperación tampoco supera el umbral → `no_evidence`.
- Embeddings: `gemini-embedding-001` (o el vigente) a 768 dims. Sin fallback a otro proveedor de embeddings: una
  dimensión o espacio distinto rompería el índice; se reintenta con backoff.
- Mock: embeddings deterministas (feature hashing de tokens → 768 dims normalizado, da similitud léxica) y
  completion determinista que cita el mejor chunk. Lo usan tests, CI y la demo offline.
- Cada llamada emite un span con `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`, `provider`, `fallback_used` y latencia.

### 7.6 Frontend
- Vista **Documentos**: formulario de subida (archivo + `doc_type` + póliza/fecha opcionales), validación de
  tipo/tamaño en cliente, tabla con badge de estado, polling cada 2 s mientras haya documentos no terminales,
  botón "Reintentar" en `failed`.
- Vista **Asistente**: filtros, input de pregunta y polling de la respuesta. Tarjeta de respuesta con citas
  clicables que abren un panel con el PDF en `#page=N` y el fragmento. Estados diferenciados: cargando, error
  (mensaje del envelope), sin evidencia (tarjeta propia con el motivo).
- Por qué polling y no SSE: hay pocas transiciones de estado (4) que duran segundos o minutos. TanStack Query
  `refetchInterval` deja de consultar al llegar a un estado terminal. SSE necesitaría puente Redis pub/sub →
  API, conexiones largas y configuración del proxy, sin beneficio para el usuario en este caso.
- Producción: build estático servido por nginx, que hace proxy de `/api` → api.

### 7.7 Observabilidad
- OTel SDK en API y worker; instrumentación de FastAPI, Celery (propaga el contexto en los headers de la
  tarea), SQLAlchemy y Redis. Spans manuales: `ingest.parse`, `ingest.chunk`, `ingest.embed`,
  `retrieval.search` (filtros sin valores sensibles, hits, top score), `llm.completion`, `answer.validate`.
- Logs JSON (structlog) con `trace_id`/`span_id`. Nunca se loguea texto de chunks, preguntas ni respuestas:
  solo ids, conteos, longitudes y duraciones.
- Collector: config `local` (→ Jaeger, UI en :16686) o `datadog` (exporter Datadog con `DD_API_KEY`/`DD_SITE`),
  elegida con `OTEL_COLLECTOR_CONFIG`. El código solo habla OTLP, sin vendor lock-in.

### 7.8 Seguridad
- Upload: extensión `.pdf` + magic bytes `%PDF-` + tamaño ≤ `MAX_UPLOAD_MB` (25) + límite de páginas. Se
  escribe en streaming mientras se calcula el sha256.
- Secretos solo por env; `.env.example` sin valores reales; `.env` en `.gitignore`.
- CORS limitado al origen del frontend; contenedores sin root; validación de la pregunta (longitud 3–1000,
  sin caracteres de control).

## 8. Tests, eval y CI

- **Unitarios**: chunking (páginas, tablas con cabecera repetida), validador de citas (chunk ajeno, quote
  inventada, cero citas → no_evidence), guardas (sanitización de delimitadores, detector de injection, filtro
  de output), PII, file validation, fusión RRF.
- **Integración** (testcontainers `pgvector/pgvector:pg17`, Celery eager, `LLM_PROVIDER=mock`): ingesta real
  con Docling de los samples → `ready` con chunks y páginas; idempotencia (subida duplicada; claim
  probado directamente contra la DB con dos `task_id` distintos y con el mismo, porque el modo eager no
  ejercita `acks_late` ni el backoff real); PDF
  corrupto → `failed`; versionado; pregunta end-to-end con citas; documento malicioso no altera la respuesta;
  pregunta fuera de dominio → `no_evidence`; caída del proveedor (mock que falla) → reintento/`failed` con
  envelope.
- **Eval** (`backend/eval/dataset.yaml`, 12–15 preguntas): respuesta esperada (keywords), documento + página
  fuente, incluye preguntas sin evidencia y una sobre el documento malicioso. Métricas: hit@k y MRR
  (recuperación), fidelidad de citas (quote presente en el chunk y página esperada), acierto de respuesta,
  precisión/recall de rechazos. `run_eval.py` genera `report.md` + JSON. El reporte de ejemplo se genera
  con Gemini real; en CI se ejecuta en modo mock como smoke test.
- **CI** (GitHub Actions): backend (ruff check/format, mypy, pytest con Docker del runner, caché de modelos
  Docling) y frontend (eslint, tsc, vitest, build).

## 9. Decisiones de producto (aprobadas)

| Tema | Decisión |
|------|----------|
| Metadatos | Los pone el ajustador al subir (`doc_type` obligatorio). Si falta el nº de póliza, se extrae con regex. |
| Chunking | HybridChunker de Docling por estructura, ~512 tokens, contexto de encabezados en vez de solape fijo, tablas en chunk propio. |
| Recuperación | Híbrida vector (HNSW coseno) + FTS español con RRF. HNSW porque no necesita entrenamiento y tiene buen recall sin tuning. |
| Sin evidencia | Se prefieren falsos rechazos. Tres guardas: umbral de recuperación, `no_evidence` del LLM y validador de citas. |
| Duplicados | Mismo sha256 = mismo documento (idempotente). |
| Versiones | Se conservan todas; la anterior queda `is_current=false`; la búsqueda usa vigentes por defecto; las citas apuntan a la versión exacta. |
| PII | Regex de DNI/NIE, email, teléfono e IBAN → placeholders antes de embeddings/LLM; original solo en la DB interna. Nombres/direcciones (NER) fuera del MVP. |
| Auth | Sin auth en el MVP (limitación documentada). |
| Samples | PDFs sintéticos de Tramontana generados por script: póliza hogar con tabla, condicionado, carta de siniestro, escaneado (solo imagen), corrupto, malicioso. |

## 10. A decidir por quien construye

Versiones exactas de librerías, nombres internos, prompts concretos, valores de umbral (calibrados con el
eval), diseño visual de la UI dentro de lo descrito, y la redacción de README/DECISIONS. La sección de uso de
IA en DECISIONS.md se redacta a partir de notas tomadas durante el trabajo (qué se corrigió o descartó).
