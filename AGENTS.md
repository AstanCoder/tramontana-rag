# AGENTS.md

Guía para agentes de código (Claude Code, Codex, Copilot y similares) que trabajen en este repositorio.
Complementa a `README.md` (uso y demo) y `DECISIONS.md` (decisiones y trade-offs). Si este documento y el
código discrepan, manda el código; corrige este documento.

## 1. Contexto del proyecto

- **Qué es:** MVP de asistente RAG interno para ajustadores de una aseguradora ficticia (Tramontana Seguros).
  Ingesta PDFs de pólizas, condicionados y cartas de siniestro (incluidos escaneados y con tablas) y responde
  preguntas en lenguaje natural citando documento, página y fragmento. Rechaza cuando no hay evidencia.
- **Origen:** prueba técnica (Ingeniero AI Full Stack Semi-Senior). El enunciado está en
  `prueba-tecnica-topdevcrew.pdf`, en la raíz, **excluido de git a propósito** porque contiene el enlace de
  entrega. Léelo antes de cambiar el alcance.
- **Repositorio:** `https://github.com/AstanCoder/tramontana-rag` (público, monorepo, rama `main`).
- **Restricciones de la entrega:**
  - Fecha límite: **2026-11-05 23:17 UTC**.
  - Hay **una sola entrega**: se congela el commit `HEAD` de `main` cuando el usuario pega el enlace.
  - **El agente nunca envía la entrega**; la hace el usuario.
  - `main` debe estar siempre en verde y en estado entregable.

## 2. Estado actual

Todos los requisitos del enunciado están implementados, revisados y verificados (ver `README.md`, secciones
"Qué está hecho" y "Limitaciones conocidas"). CI en verde. Se hizo un smoke test con `docker compose` desde un
clon limpio.

Trabajo pendiente, por prioridad:

1. **Ejecutar el camino Gemini de extremo a extremo.** Requiere `.env` con `LLM_PROVIDER=litellm` y
   `GEMINI_API_KEY` (la crea el usuario; nunca se pega en el chat ni se commitea).
   1. Listar los modelos disponibles para la key y ajustar `LLM_MODEL`, `LLM_FALLBACK_MODELS` y
      `EMBEDDING_MODEL` en `.env.example` si hace falta.
   2. Ejecutar `docker compose down -v` y después el eval con `GIT_SHA` (comando en la §4).
   3. Calibrar `EVIDENCE_MIN_SIMILARITY` para LiteLLM. Regla: el valor más bajo con precisión de rechazos 1.0
      que no rechace preguntas respondibles. Ajustar el default por proveedor en `backend/app/config.py`.
   4. Commitear el reporte real (`backend/eval/report.md` y `report.json`).
   5. Verificar en Jaeger: `gen_ai.*`, `llm.fallback_used` (forzar con `LLM_MODEL=gemini/does-not-exist`) y
      `question.model`/`provider`.
   6. Actualizar las listas de "sin verificar" en `README.md` y `DECISIONS.md` con lo observado. Incluye qué
      error devuelve Gemini ante una key inválida: si es HTTP 400, mapearlo o documentarlo.
2. **Pregunta atascada tras la muerte del worker** (DECISIONS §7): token de dueño para preguntas, como en los
   documentos, con una migración nueva.
3. **Opcionales de baja prioridad**, detectados en revisión y no bloqueantes:
   - etiquetas de imágenes con patch flotante (`redis`, `node`, `nginx`);
   - `useDocuments` sigue haciendo polling tras un error si hay filas pendientes en caché;
   - la regla de evidencia por número de 3+ cifras admite cualquier coincidencia full-text;
   - el instrumentador de FastAPI registra el mensaje de excepciones no controladas en el span HTTP.

   Cualquier cambio aquí debe mantener CI en verde y actualizar la documentación afectada.

## 3. Estructura del repositorio

```
backend/                 Python 3.12 · FastAPI · Celery · SQLAlchemy 2.0 · Alembic · Docling · LiteLLM
  app/main.py            create_app() (factory), routers, handlers de error, OTel
  app/worker.py          app de Celery (pool prefork; señales de logging y telemetría)
  app/config.py          Settings (pydantic-settings); toda la configuración por variables de entorno
  app/{db,models,schemas,errors,logging,telemetry,pii,guards}.py
  app/documents/         subida validada, almacenamiento, dedupe por sha256, retry, servir PDF
  app/ingestion/         parse (Docling + Tesseract), chunking, claim con token de dueño, tarea Celery
  app/retrieval/         búsqueda híbrida (HNSW + FTS español, RRF), filtros y puerta de evidencia
  app/answering/         prompt con delimitadores, validación de citas, fallback extractivo, API de preguntas
  app/llm/               Gateway (Protocol), LiteLLMGateway (Router + fallbacks) y MockGateway determinista
  alembic/versions/      migraciones 0001 y 0002 (escritas a mano)
  eval/                  dataset.yaml (13 preguntas), run_eval.py, report.md/json
  tests/unit, tests/integration
frontend/                React · TypeScript · Vite · TanStack Query · Tailwind v4 · Vitest · nginx
infra/otel/              configuración del collector: local (Jaeger) y datadog
samples/                 PDFs sintéticos + generate.py + manifest.yaml + README (datos y páginas clave)
docs/design.md           especificación aprobada (registro histórico)
docs/plan.md             plan de implementación por tareas (registro histórico)
docker-compose.yml       api, worker (cola celery), worker-questions (cola questions), postgres+pgvector, redis,
                         frontend, otel-collector, jaeger
.github/workflows/ci.yml lint + tipos + tests de backend y frontend
```

## 4. Entorno y comandos

**Requisitos:**
- Para ejecutar el stack: Docker con Compose v2.
- Para desarrollar en local: `uv` (gestiona Python 3.12), Node 24 + pnpm, y Tesseract con el idioma `spa`
  (`brew install tesseract tesseract-lang`).
- La primera ejecución descarga los modelos de Docling y Hugging Face.

```bash
# Stack completo (proveedor mock por defecto, sin claves)
docker compose up --build
docker compose down -v                     # obligatorio al cambiar de proveedor de embeddings

# Backend
cd backend && uv sync
uv run pytest                              # unitarios + integración (testcontainers: requiere Docker)
uv run pytest -m "not integration"         # sin Docker
uv run ruff check . && uv run ruff format --check . && uv run mypy app

# Frontend
cd frontend && pnpm install --frozen-lockfile
pnpm lint && pnpm typecheck && pnpm test && pnpm build

# Evaluación (con el stack levantado)
docker compose run --rm -e GIT_SHA=$(git rev-parse --short HEAD) api python -m eval.run_eval --reset

# Regenerar los samples (deterministas por máquina; los PDF commiteados son los canónicos)
uv run --directory backend python ../samples/generate.py --out ../samples
```

URLs locales: UI `http://localhost:8080`, API `http://localhost:8000/docs`, Jaeger `http://localhost:16686`.

Las variables de entorno están documentadas en `README.md` y `.env.example`. Toda variable nueva se declara en
`backend/app/config.py` (`Settings`), se añade a `.env.example` y al README, y a `docker-compose.yml` si el
contenedor la necesita.

## 5. Forma de trabajo

### 5.1 Flujo general

1. **Entender antes de cambiar.** Leer el enunciado, `DECISIONS.md` y el código afectado. Para cambios de
   comportamiento, acordar el diseño con el usuario antes de escribir código.
2. **Planificar.** Dividir el trabajo en tareas pequeñas. Cada tarea tiene un entregable que se puede probar
   por sí solo, archivos concretos, interfaces exactas y los tests que la prueban.
3. **Implementar con TDD.** Escribir el test, verlo fallar por el motivo esperado, implementar lo mínimo y
   verlo pasar.
4. **Verificar antes de commitear:** suite completa + ruff + format + mypy (backend), y lint + typecheck +
   test + build (frontend).
5. **Revisar.** Cada tarea pasa una revisión independiente de cumplimiento de spec y de calidad. Los hallazgos
   críticos e importantes se corrigen antes de seguir; los menores se anotan.
6. **Documentar.** Toda decisión no trivial, o desviación del diseño, se registra en `DECISIONS.md` con la
   decisión, el motivo y el coste si resulta equivocada. Cualquier corrección hecha a una sugerencia de IA se
   añade a la sección "Uso de IA generativa".

### 5.2 Ejecución con subagentes (método usado en este repo)

El proyecto se construyó con *subagent-driven development*. Si se retoma con el mismo método:

- **Controlador:** prepara un brief por tarea (requisitos exactos y restricciones globales), despacha un
  implementador, despacha después un revisor con el diff y decide sobre los conflictos.
- **Ledger:** el controlador mantiene un registro de progreso fuera de git (`.superpowers/`, ignorado). Anota
  qué tarea está completa, en qué commits, y cada decisión con el formato `Ruling: <qué> — <por qué> — <coste
  si es erróneo>`.
- **Implementador:** trabaja solo la tarea asignada, no lanza subagentes, aplica TDD y entrega un informe con
  evidencias (comando y salida de RED/GREEN).
- **Revisor:** es de solo lectura. Emite dos veredictos (cumplimiento de spec y calidad) y gradúa la severidad
  como Critical, Important o Minor.
- **Rondas de corrección:** como máximo 5 por tarea. Cada ronda termina con una re-revisión acotada al diff de
  la corrección.
- **Revisión final:** al terminar todas las tareas, una revisión de toda la rama con el modelo más capaz,
  seguida de una sola ola de correcciones y su re-revisión.
- **Modelos:** el más capaz para diseño, para tareas delicadas (concurrencia, seguridad, telemetría) y para la
  revisión final; uno intermedio para el resto.

### 5.3 Criterios de calidad

- Producción, limpio y simple: nada de features, opciones ni abstracciones que no pida el enunciado.
- Reutilizar lo que ya existe (helpers, patrones, fixtures) antes de añadir código o dependencias.
- Corregir la causa raíz en el código compartido, no parchear en los llamadores.
- Las APIs de librerías (Docling, LiteLLM, OpenTelemetry, pgvector) se verifican contra la documentación
  actual o el paquete instalado, no de memoria.

## 6. Convenciones de código

- **Idioma:** código, identificadores y mensajes de commit en inglés. UI, `README.md`, `DECISIONS.md`, este
  documento y los samples, en español con ortografía completa.
- **Backend:**
  - FastAPI y SQLAlchemy síncronos en todo el código (Celery es síncrono).
  - Contratos de API en `app/schemas.py` (Pydantic v2).
  - Errores con un único envelope `{"error": {"code", "message", "trace_id"}}`; los 500 llevan mensaje
    genérico.
  - ruff (línea de 100) y mypy estricto sobre `app/`.
- **Base de datos:** todo cambio de esquema va con una migración Alembic nueva (no se editan las existentes) y
  mantiene en verde `test_models_match_migration`.
- **Frontend:**
  - Los tipos en `src/api/types.ts` reflejan exactamente `app/schemas.py`.
  - Sin router, librería de estado ni kit de UI adicionales.
  - Texto siempre renderizado como texto plano (nunca `dangerouslySetInnerHTML`).
  - Accesible: labels, foco visible, `aria-live` en estados.
- **Dependencias:**
  - Versiones exactas en los lockfiles (`uv.lock`, `pnpm-lock.yaml`).
  - `litellm` fijado a una versión verificada; **nunca 1.82.7 ni 1.82.8**, que estaban comprometidas.
  - SQLAlchemy `<2.1`, por compatibilidad con la instrumentación de OpenTelemetry.
- **Comentarios:** solo el porqué que el código no muestra. Los atajos con límite conocido se marcan como
  `shortcut: <límite>, <cuándo mejorarlo>`.

## 7. Invariantes que no se deben romper

- **Citas:** ninguna respuesta `answered` llega al usuario sin al menos una cita validada. La cita debe
  referirse a un chunk recuperado y contener texto literal normalizado. El documento y la página los pone el
  servidor, nunca el LLM.
- **Contenido no confiable:** al LLM solo se envía `text_redacted`, saneado y entre delimitadores
  `<doc-{nonce} …>` con un nonce por petición. Nunca `Chunk.text` ni los headings sin redactar.
- **PII:** se redacta DNI/NIE, IBAN, email y teléfono antes de embeddings y LLM. El original solo vive en la
  base de datos interna.
- **Logs y trazas:** sin texto de chunks, preguntas, respuestas, PII ni mensajes de excepción. Usar
  `content_free_span` para los spans nuevos; el engine usa `hide_parameters=True`.
- **Concurrencia:**
  - La ingesta solo escribe con el token de dueño (`ingest_task_id`) y bloqueo `FOR UPDATE`.
  - Leases: 20 min para documentos y 10 min para preguntas, siempre por encima de los límites duros de Celery
    (960 s y 540 s).
- **Espacio de embeddings:** cada documento registra `embedding_model` y la recuperación filtra por el modelo
  activo.
- **Defaults:** el stack funciona sin `.env` (`LLM_PROVIDER=mock`). Los tests no usan red ni claves.

## 8. Seguridad

- Secretos solo por variables de entorno. `.env` está en `.gitignore`. No escribir claves en código, tests,
  logs, commits ni en el chat.
- Antes de cada push, ejecutar `git grep -nE 'AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{20,}'`; el resultado
  debe salir vacío.
- Validar en la frontera de confianza: tipo y magic bytes del PDF, tamaño (`MAX_UPLOAD_MB`), páginas
  (`MAX_PAGES`) y longitud y caracteres de la pregunta.
- Los documentos subidos son contenido no confiable. Ver `DECISIONS.md` §13 (defensa por capas) y el sample
  malicioso `samples/poliza-comercio-maliciosa-TRM-COM-2024-00999.pdf`.

## 9. Git, commits y CI

- Rama única `main`. Commits pequeños y atómicos con Conventional Commits (`feat:`, `fix:`, `docs:`, `ci:`,
  `chore:`), en inglés.
- **Autor:** `AstanCoder <111539332+AstanCoder@users.noreply.github.com>`, configurado en el repo local.
  Trailer al final del mensaje:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (o el modelo que haya escrito el cambio).
- **Cuenta de `gh`:** debe ser **AstanCoder** (comprobar con `gh api user --jq .login`). No cambiar de cuenta
  sin pedírselo al usuario.
- **Push:** solo con CI previsiblemente en verde. Tras cada push, `gh run watch` hasta que termine en verde.
- **Prohibido:** reescribir historia publicada (`push --force`, rebase de `main`) y saltarse hooks.
- **CI** (`.github/workflows/ci.yml`): backend ejecuta ruff, format, mypy y pytest completo con
  testcontainers; frontend ejecuta lint, typecheck, vitest y build. No se definen secretos y los permisos son
  `contents: read`.

## 10. Referencias

| Documento | Para qué |
|---|---|
| `README.md` | arranque, variables, demo guiada, tests, eval, limitaciones |
| `DECISIONS.md` | decisiones del enunciado, trade-offs, uso de IA y lo corregido |
| `docs/design.md` | especificación aprobada (histórica) |
| `docs/plan.md` | plan por tareas y restricciones globales (histórico) |
| `samples/README.md` | datos y páginas clave de cada PDF de ejemplo |
| `backend/eval/report.md` | último reporte de evaluación (actualmente del proveedor mock) |
