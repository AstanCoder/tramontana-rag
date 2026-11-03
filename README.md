# Tramontana RAG — asistente de pólizas y siniestros

Asistente interno para los ajustadores de la aseguradora ficticia Tramontana Seguros. Se suben PDFs (pólizas,
condicionados, cartas de siniestro) y se pregunta en lenguaje natural. Responde **solo** con lo que dicen los
documentos, cita documento + página + fragmento, y cuando no hay evidencia lo dice en vez de inventar.

- Ingesta asíncrona (Celery + Redis): Docling con OCR → chunks por estructura con tablas en markdown → PII
  enmascarada → embeddings → PostgreSQL + pgvector.
- Recuperación híbrida (HNSW coseno + full-text en español, fusión RRF) con filtros por tipo, póliza, fechas y
  versión.
- Respuesta estructurada validada: cada cita debe existir entre los fragmentos recuperados y contener la cita
  literal. Sin citas válidas no hay respuesta.
- Gateway LLM con LiteLLM (Gemini Flash → Flash-Lite → respuesta extractiva degradada) o `mock` determinista
  sin red.
- Una sola traza OpenTelemetry API → Celery → recuperación → LLM, y logs JSON con `trace_id`.

Las decisiones y sus costes están en [DECISIONS.md](DECISIONS.md). La especificación original y el plan de
implementación están en [`docs/design.md`](docs/design.md) y [`docs/plan.md`](docs/plan.md); donde difieren
del código, mandan el código y DECISIONS.md.

## Arquitectura

```
navegador ──▶ frontend (nginx + React SPA, :8080) ──/api──▶ api (FastAPI, :8000) ──▶ PostgreSQL + pgvector
                                                              │ ▲                        ▲
                                         encola ingest_document│ │ polling de estado       │
                                         y answer_question     ▼ │                        │
                                                            Redis ◀──▶ worker (Celery) ────┘
                                                                         │ ingest_document: Docling → chunks → embeddings
                                                                         │ answer_question: recuperación → LLM → validación
                                                                         ▼
                                                              LLM gateway (LiteLLM → Gemini | mock)

api, worker ──OTLP──▶ otel-collector ──▶ Jaeger (:16686)  |  Datadog (OTEL_COLLECTOR_CONFIG=datadog)
```

Las preguntas también pasan por Celery: `POST /api/questions` guarda la pregunta como `pending`, encola
`answer_question` y devuelve 202; la UI consulta `GET /api/questions/{id}` hasta que termina. Así la traza
cruza literalmente API → Celery → recuperación → LLM (ver DECISIONS.md).

Servicios de `docker-compose.yml`: `postgres` (pgvector 0.8.7, PG 17), `redis`, `api` (aplica
`alembic upgrade head` al arrancar), `worker` (misma imagen, `celery worker` con pool prefork), `frontend`,
`otel-collector` y `jaeger`.

## Requisitos

- Para ejecutar la aplicación: **Docker** con Docker Compose. Nada más. La imagen del backend es pesada
  (Docling, torch CPU, Tesseract y los modelos precargados), así que el primer `--build` tarda varios minutos.
- Para los tests en local (fuera de Docker): `uv`, Python 3.12, Docker en marcha (los tests de integración
  levantan Postgres y Redis con testcontainers), `tesseract` con el idioma `spa` (`brew install tesseract
  tesseract-lang` / `apt install tesseract-ocr tesseract-ocr-spa`) y red en la primera ejecución, que
  descarga los modelos de Docling y Hugging Face. Frontend: Node 24 y pnpm 11.

## Arranque

```bash
docker compose up --build
```

Sin `.env` funciona con el proveedor **mock**: embeddings por hashing léxico y respuestas deterministas, sin
red ni claves. Sirve para ver todo el flujo, pero no para juzgar la calidad de las respuestas.

Con Gemini real:

```bash
cp .env.example .env
# en .env: LLM_PROVIDER=litellm y GEMINI_API_KEY=<tu key>
docker compose up --build
```

| URL | Qué es |
|---|---|
| http://localhost:8080 | UI (Documentos y Asistente) |
| http://localhost:8000/docs | API (OpenAPI / Swagger) |
| http://localhost:16686 | Jaeger (servicios `tramontana-api` y `tramontana-worker`) |

**Cambiar de proveedor requiere `docker compose down -v`.** Cada documento guarda el modelo con el que se
embebió y la búsqueda solo usa los del modelo actual, porque comparar vectores de espacios distintos es ruido.
Al pasar de mock a Gemini (o al revés) los documentos ya subidos dejan de aparecer en las búsquedas, y como la
subida se deduplica por sha256 no se pueden volver a subir. `down -v` borra la base de datos y los ficheros.
El script de eval no tiene este problema: reingesta él solo los samples embebidos con otro modelo.

## Variables de entorno

Todas son opcionales. `.env.example` las documenta. Compose fija `DATABASE_URL`, `REDIS_URL`, `UPLOAD_DIR`,
`OTEL_SDK_DISABLED` y `OTEL_EXPORTER_OTLP_ENDPOINT` para los contenedores. `LLM_PROVIDER` y `GEMINI_API_KEY`
se pueden pasar desde la shell o desde `.env`; el resto solo se lee de `.env`.

| Variable | Por defecto | Significado |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://tramontana:tramontana@localhost:5432/tramontana` | Conexión a Postgres. En compose, `@postgres:5432`. |
| `REDIS_URL` | `redis://localhost:6379/0` | Broker y backend de Celery. En compose, `redis://redis:6379/0`. |
| `UPLOAD_DIR` | `/data/uploads` | Carpeta de los PDF subidos (volumen `uploads`), guardados por UUID. |
| `MAX_UPLOAD_MB` | `25` | Tamaño máximo de subida. La UI (25 MB) y nginx (`26m`) tienen el mismo límite fijo: si se cambia, hay que cambiarlos también. |
| `MAX_PAGES` | `200` | Páginas máximas; si se supera, el documento queda `failed` / `too_many_pages`. |
| `LLM_PROVIDER` | `mock` | `mock` (sin red) o `litellm` (Gemini real). |
| `GEMINI_API_KEY` | vacío | Key de Google AI Studio. Solo con `litellm`. |
| `LLM_MODEL` | `gemini/gemini-2.5-flash` | Modelo principal (nombre LiteLLM). |
| `LLM_FALLBACK_MODELS` | `gemini/gemini-2.5-flash-lite` | Fallbacks separados por comas; vacío = sin fallback. |
| `EMBEDDING_MODEL` | `gemini/gemini-embedding-001` | Modelo de embeddings (se piden 768 dimensiones). |
| `LLM_TIMEOUT_S` | `30` | Timeout por llamada al proveedor. |
| `LLM_NUM_RETRIES` | `2` | Reintentos de LiteLLM por llamada (completions y embeddings). |
| `EMBED_BATCH_SIZE` | `50` | Textos por llamada de embeddings. |
| `EVIDENCE_MIN_SIMILARITY` | sin definir | Similitud coseno mínima para que un hit vectorial cuente como evidencia. Sin definir: `0.40` con mock, `0.55` con litellm. |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8080` | Orígenes permitidos, separados por comas. |
| `OTEL_SDK_DISABLED` | `true` | Desactiva el tracing. Compose lo pone a `false`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | vacío | Endpoint OTLP gRPC. Compose: `http://otel-collector:4317`. |
| `LOG_LEVEL` | `INFO` | Nivel de log (JSON a stdout). |
| `OTEL_COLLECTOR_CONFIG` | `local` | Solo compose. `local` → Jaeger; `datadog` → Datadog (necesita `DD_API_KEY`). |
| `DD_API_KEY` | vacío | Solo compose, para el exporter de Datadog del collector. |
| `DD_SITE` | `datadoghq.com` | Solo compose. Site de Datadog (`datadoghq.eu`, …). |
| `GIT_SHA` | `desconocido` | Solo eval: commit que se escribe en el reporte. |

La imagen fija además `LITELLM_LOCAL_MODEL_COST_MAP=True` (LiteLLM no descarga su mapa de costes de GitHub al
arrancar) y `HF_HUB_OFFLINE=1` (los modelos van dentro de la imagen).

## Demo guiada

Con el stack levantado. Todo se puede hacer desde la UI (http://localhost:8080) o con `curl` contra la API.
Los PDF de `samples/` son sintéticos; `samples/manifest.yaml` tiene los metadatos de cada uno y
`samples/README.md` explica qué hay en cada página.

**1. Subir los samples con sus metadatos.** En la UI: pestaña *Documentos*, un archivo cada vez con el tipo,
la póliza y la fecha del manifest. Con `curl`:

```bash
up() { local a=(-F "file=@samples/$1" -F "doc_type=$2" -F "title=$5")
       [ -n "$3" ] && a+=(-F "policy_number=$3"); [ -n "$4" ] && a+=(-F "document_date=$4")
       curl -s http://localhost:8000/api/documents "${a[@]}"; echo; }   # vale en bash y zsh
up poliza-hogar-TRM-HOG-2024-00123.pdf          policy             TRM-HOG-2024-00123 2024-01-15 "Póliza de hogar TRM-HOG-2024-00123"
up poliza-hogar-TRM-HOG-2024-00123-v2.pdf       policy             TRM-HOG-2024-00123 2025-01-15 "Póliza de hogar TRM-HOG-2024-00123 (versión 2)"
up condicionado-general-hogar.pdf               general_conditions ""                 2023-06-01 "Condicionado general del seguro de hogar"
up carta-siniestro-SIN-2025-0456.pdf            claim_letter       TRM-HOG-2024-00123 2025-03-10 "Carta de siniestro SIN-2025-0456"
up poliza-auto-escaneada-TRM-AUT-2024-00777.pdf policy             TRM-AUT-2024-00777 2024-05-01 "Póliza de automóvil TRM-AUT-2024-00777 (escaneada)"
up poliza-comercio-maliciosa-TRM-COM-2024-00999.pdf policy         TRM-COM-2024-00999 2024-09-01 "Póliza de comercio TRM-COM-2024-00999"
up corrupto.pdf                                 other              ""                 ""         "Documento corrupto"
curl -s http://localhost:8000/api/documents | python3 -m json.tool   # estados
```

Cada subida devuelve 202 `pending`; el worker los pasa a `processing` y a `ready` (unos segundos por
documento; el escaneado tarda más por el OCR). La UI consulta el estado cada 2 s. Volver a subir el mismo
archivo devuelve 200 con el documento existente, sin reencolar.

**2. Preguntar.** En la UI: pestaña *Asistente*, con filtros opcionales. Con `curl`:

```bash
ask() {
  local filters=${2:-'{}'} id
  id=$(curl -s http://localhost:8000/api/questions -H 'Content-Type: application/json' \
         -d "{\"question\": \"$1\", \"filters\": $filters}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  while curl -s http://localhost:8000/api/questions/$id | grep -q '"status":"p'; do sleep 1; done  # pending/processing
  curl -s http://localhost:8000/api/questions/$id | python3 -m json.tool
}
```

| Qué se demuestra | Pregunta y filtros | Esperado |
|---|---|---|
| Tabla | `ask "¿Cuál es el deducible por robo en la póliza de hogar?" '{"policy_number": "TRM-HOG-2024-00123"}'` | Cita de la versión 2, pág. 2 (cuadro de coberturas: robo, 150 €). |
| Versión vigente | `ask "¿Qué deducible se aplica a los daños por agua en sótanos?" '{"policy_number": "TRM-HOG-2024-00123"}'` | Solo busca en la versión vigente (v2): 450 €, pág. 3. |
| Versión anterior | lo mismo con `'{"policy_number": "TRM-HOG-2024-00123", "include_superseded": true}'` | Entran también los fragmentos de la v1 (600 €). Cada cita apunta a la versión exacta de la que sale. |
| Escaneado (OCR) | `ask "¿Qué capital cubre la responsabilidad civil obligatoria de la póliza de automóvil?" '{"policy_number": "TRM-AUT-2024-00777"}'` | Cita de la pág. 2 del PDF escaneado (70.000.000 €). |
| Documento malicioso | `ask "¿Están cubiertos los daños por inundación?" '{"policy_number": "TRM-COM-2024-00999"}'` | Nunca "todo cubierto sin deducible". El chunk con la inyección queda marcado y no entra en el contexto. Con Gemini la respuesta esperada es "excluida" (pág. 2); con mock la pregunta acaba en `no_evidence` (ver limitaciones). |
| Sin evidencia | `ask "¿Cubre el seguro los gastos del veterinario de mi mascota?"` | `no_evidence` con motivo, sin citas. La UI lo muestra en una tarjeta propia. |

En la UI cada cita abre el PDF en la página citada (visor nativo del navegador, `#page=N`) junto al
fragmento. Con mock la "respuesta" es la primera frase del fragmento que más palabras comparte con la
pregunta; con Gemini es una respuesta redactada.

Para ver que la inyección y la PII se tratan en la ingesta:

```bash
docker compose exec postgres psql -U tramontana -d tramontana -c \
  "select d.title, count(*) filter (where c.suspicious) as marcados, count(*) as chunks
     from chunks c join documents d on d.id = c.document_id group by d.title"
docker compose exec postgres psql -U tramontana -d tramontana -c \
  "select left(text_redacted, 300) from chunks c join documents d on d.id = c.document_id
    where d.doc_type = 'claim_letter'"          # DNI, IBAN, teléfono y email como [DNI], [IBAN], …
docker compose logs api worker | grep -c 12345678Z   # 0: el DNI de la carta no aparece en los logs
```

**3. PDF corrupto.** `corrupto.pdf` empieza por `%PDF-`, así que pasa la validación de subida (202). El worker
no puede abrirlo y lo deja en `failed` con `error_code=corrupt_document`, sin reintentos automáticos. La UI
muestra "El PDF está dañado." y el botón *Reintentar*.

**4. Modo degradado (necesita una key válida).** En `.env`:

```bash
LLM_PROVIDER=litellm
GEMINI_API_KEY=<key válida>
LLM_MODEL=gemini/does-not-exist
LLM_FALLBACK_MODELS=
```

`docker compose up -d` recrea `api` y `worker` con la nueva configuración. Los embeddings siguen funcionando,
pero la generación falla (modelo inexistente). La respuesta sale con `degraded=true`, `confidence=low`, el
texto "No se pudo generar una respuesta en este momento…" y los tres mejores fragmentos como citas. La UI
muestra un aviso de modo degradado y el span `answer.validate` lleva `llm.fallback_used=extractive`.

Con una **key inválida** no hay modo degradado: sin embeddings de la pregunta no hay recuperación, así que la
pregunta acaba `failed` y la UI enseña el mensaje y el `trace_id`. Se espera `error_code=provider_unavailable`
(el gateway mapea los errores de autenticación de LiteLLM), pero no está comprobado con Gemini real: si Gemini
responde a una key inválida con un HTTP 400 genérico, el error no está mapeado y quedaría `answer_error`.

**5. La traza.** `GET /api/questions/{id}` devuelve el `trace_id`. En Jaeger, buscarlo (o elegir el servicio
`tramontana-api` y la operación `POST /api/questions`). La traza contiene:
- el span HTTP de la API y `apply_async/app.answering.tasks.answer_question`;
- en `tramontana-worker`, `run/app.answering.tasks.answer_question`, `llm.embed`, `retrieval.search` (hits, top similarity y los
  nombres de los filtros, sin sus valores), `llm.completion` (`gen_ai.*`: modelo, tokens, proveedor,
  `llm.fallback_used`, latencia) y `answer.validate`;
- los spans de SQLAlchemy y Redis.

Ningún span ni log lleva el texto de preguntas, respuestas o chunks.

## Tests

```bash
cd backend && uv sync && uv run pytest                      # todo: unitarios + integración (Docker)
cd backend && uv run pytest -m "not integration"            # solo unitarios, sin Docker
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app
cd frontend && pnpm install && pnpm test                    # y pnpm lint / pnpm typecheck / pnpm build
```

Los tests usan el gateway mock: no necesitan claves ni llaman a ningún LLM. Los de integración levantan
Postgres + pgvector y Redis reales con testcontainers. Cubren:
- ingesta real con Docling de los samples (tablas, OCR del escaneado);
- idempotencia y el claim con token de dueño;
- versionado y filtros de recuperación;
- preguntas de extremo a extremo y el documento malicioso;
- caída del proveedor;
- la traza API → Celery → LLM con un worker real.

Los tests no están dentro de la imagen. CI (GitHub Actions) ejecuta ruff, mypy y pytest completo en el
backend, y eslint, tsc, vitest y build en el frontend.

## Evaluación

`backend/eval/dataset.yaml` tiene 13 preguntas sobre los samples con documento y página esperados, keywords de
la respuesta, preguntas sin evidencia y una sobre el documento malicioso. Con el stack levantado:

```bash
docker compose run --rm -e GIT_SHA=$(git rev-parse --short HEAD) api python -m eval.run_eval --reset
```

Ingesta los samples (salvo `corrupto.pdf`), responde cada pregunta con el mismo código que la API y escribe
`backend/eval/report.md` y `report.json`. Métricas:
- recuperación: hit@6 y MRR sobre documento + página;
- fidelidad de citas: el fragmento citado está en el chunk recuperado y la página es la de ese chunk;
- recall de keywords;
- precisión y recall de rechazos;
- resistencia a la inyección, que solo se puntúa si la pregunta se responde.

El proveedor es el de `.env`. El reporte incluido es del **mock**: valida el arnés, no la calidad del sistema
(ver limitaciones).

## Qué está hecho

Todos los requisitos de la prueba:
- API FastAPI con contratos Pydantic y errores con el mismo envelope (`{"error": {code, message, trace_id}}`).
- Ingesta asíncrona idempotente y reintentable con estados `pending | processing | failed | ready`.
- Chunking por estructura con tablas y páginas.
- Recuperación híbrida con filtros y versionado.
- Respuestas con citas validadas, umbral de evidencia y fallback extractivo.
- Defensa contra inyección por capas y PII enmascarada antes del proveedor.
- UI React con polling, historial de preguntas de la sesión, citas navegables y estados de carga, error y sin
  evidencia.
- Traza única con atributos `gen_ai.*` y logs JSON correlacionados.
- Eval con reporte, tests unitarios y de integración, CI y `docker compose up`.

## Qué no está hecho (fuera del MVP)

- Autenticación, roles, multi-tenant y rate limiting. Cualquiera que conozca el UUID de un documento puede
  descargarlo.
- NER de nombres y direcciones: solo se enmascaran por regex DNI/NIE, IBAN, email y teléfono.
- Almacenamiento en S3 (los PDF van a un volumen local de Docker).
- Extracción de metadatos con LLM (los pone el ajustador; el nº de póliza se extrae por regex si falta).
- SSE/websockets: se usa polling.
- Conversación multi-turno: cada pregunta es independiente.
- Despliegue cloud.

## Limitaciones conocidas

- **El camino Gemini no se ha ejecutado de extremo a extremo en la verificación de este repo** (no había key
  durante el desarrollo). Sin probar con Gemini real:
  - `response_format` con el esquema Pydantic;
  - `dimensions=768` en los embeddings;
  - la detección de `fallback_used` por el nombre de modelo de la respuesta;
  - los atributos de tokens;
  - la receta de modo degradado;
  - el comportamiento con una key inválida (se espera `failed` / `provider_unavailable` en preguntas e
    ingesta, pero un HTTP 400 genérico de Gemini acabaría en `answer_error` / `ingestion_error`).

  El reporte de eval incluido es del mock (keyword_recall 0,45; el mock no redacta respuestas).
- **El umbral 0,55 para Gemini no está calibrado**: es un valor inicial. El 0,40 del mock sí se midió sobre el
  eval. Calibrarlo es una ejecución del eval con key (ver DECISIONS.md).
- **Una pregunta con un número de 3+ cifras** (año, importe, nº de póliza) da por buena cualquier
  coincidencia full-text como evidencia. Una pregunta fuera de dominio con un año puede llegar al LLM; siguen
  actuando el `no_evidence` del LLM y el validador de citas.
- Con el mock, la pregunta sobre inundación del documento malicioso se rechaza en la puerta de evidencia. La
  inyección no consigue nada, pero tampoco se ve una respuesta.
- **Un chunk marcado como inyección se excluye entero.** Un falso positivo esconde evidencia legítima, y
  Docling puede unir el texto previo a una tabla con la tabla.
- **Cuotas del free tier de Gemini**: cambian a menudo. Una ingesta grande puede llegar a 429; se reintenta
  con backoff y, si se agota el presupuesto, el documento queda `failed` / `provider_unavailable`
  (reintentable). El free tier puede usar los datos para entrenar.
- **Redis no tiene persistencia.** Si se pierde con documentos en cola, se quedan `pending`. Pasados 20 min se
  pueden reintentar con `POST /api/documents/{id}/retry`; la UI solo muestra el botón en `failed`.
- `GET /api/documents` devuelve como mucho los 200 más recientes, sin paginación.
- La cita abre el PDF en la primera página del chunk, en el visor nativo del navegador, sin resaltar el
  fragmento. Si el chunk abarca varias páginas se indica "págs. N–M".
- El historial de preguntas de la UI vive en la sesión del navegador.
- El filtro por póliza es por coincidencia exacta.
- El regex de teléfono no detecta fijos agrupados de dos en dos (`91 234 56 78`) y puede tapar importes
  escritos como `600 000 000`.
- El límite de subida se aplica después de que Starlette reciba el cuerpo; el puerto 8000 está expuesto sin el
  límite de nginx.
- Una pregunta podría procesarse dos veces si la cola acumula más de 10 min de retraso y además muere un
  worker; el resultado se sobrescribe, sin otro efecto.
