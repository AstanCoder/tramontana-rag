# Decisiones

Cada decisión tiene el mismo formato: qué se decidió, por qué, qué cuesta y cómo se mide o qué pasa si es la
equivocada. La especificación de partida es [`docs/design.md`](docs/design.md). Donde el código se apartó de
ella, este documento lo explica.

## 1. Chunking y recuperación

**Decisión.**
- Chunks por estructura con el `HybridChunker` de Docling: máximo 512 tokens con el tokenizer de
  `all-MiniLM-L6-v2`, sin solape fijo.
- Cada chunk se embebe con su ruta de encabezados delante (`contextualize`).
- Las tablas van en markdown, con la cabecera repetida si se parten.
- Cada chunk guarda `page_start`/`page_end`.
- Recuperación híbrida:
  - vectorial: top 20 por coseno en HNSW;
  - full-text en español: top 20 con semántica OR;
  - fusión RRF (k=60) y top 6 al LLM.
- Filtros por tipo de documento, póliza, rango de fechas y versión vigente.

**Por qué.**
- Se optimiza que el chunk correcto llegue al top 6 *con su página*: una cita sin página no sirve al ajustador.
- Las pólizas tienen estructura fuerte (artículos, cláusulas, cuadros). Cortar por estructura mantiene una
  cláusula entera en un chunk, y los encabezados dan el contexto que el solape fijo daría a ciegas.
- El serializador de tablas por defecto del chunker produce tripletas, que el LLM lee mal. En markdown, con la
  cabecera repetida, cada fila conserva el nombre de sus columnas.
- La parte léxica existe porque las preguntas de ajustadores llevan números de póliza, importes y términos
  exactos ("bajo rasante") que los embeddings diluyen.

**Coste.**
- Docling puede unir el texto anterior a una tabla con la tabla en un mismo chunk (`kind=table`).
- Los chunks son de tamaño desigual.
- La imagen pesa más: Docling, torch CPU y modelos precargados.

**Cómo se mide.**
- El eval (`backend/eval/`) mide hit@6 y MRR sobre documento **y página** esperados. Incluye preguntas sobre
  tabla (q01, q04, q05), cláusula (q02, q03), condicionado (q06, q07), carta (q08) y escaneado (q09).
- Cada parámetro (`MAX_TOKENS`, k de RRF, top_n, solo vectorial frente a híbrida) se cambia y se vuelve a pasar
  el eval.
- Con el mock: hit@6 1,00 y MRR 0,88. Es una referencia del arnés, no de Gemini; falta la ejecución con key
  (ver README, limitaciones).

## 2. Umbral y criterio de "no hay evidencia suficiente"

**Decisión.** El negocio prefiere **falsos rechazos** a respuestas dudosas: un ajustador que lee "no hay
evidencia" busca a mano, y uno que lee una cobertura inventada paga un siniestro que no toca. Hay tres guardas
en serie:

1. **Puerta de recuperación** (`has_evidence`, antes de llamar al LLM). Hay evidencia si en el top 6 hay:
   - un hit vectorial con similitud ≥ umbral, o
   - un hit full-text con *todos* los términos de la pregunta (AND), o
   - cualquier hit full-text cuando la pregunta lleva un número de 3+ cifras (póliza, importe).

   Si no, `no_evidence` sin gastar llamada.
2. **El LLM** devuelve `no_evidence=true` si los fragmentos no bastan (lo pide el system prompt).
3. **El validador de citas.** Una cita vale solo si su `chunk_id` está entre los recuperados y la cita literal
   (≥ 8 caracteres) es substring normalizado del texto del chunk. Las inválidas se descartan.
   - Si no queda ninguna válida → `no_evidence`.
   - Si se descartó alguna → `confidence=low`.
   - Una respuesta vacía, con URLs o HTML, o de más de 2000 caracteres → se descarta.

**Umbral por proveedor.** `EVIDENCE_MIN_SIMILARITY` sin definir vale 0,40 con mock y 0,55 con Gemini.
- Los vectores del mock son hashing de palabras y también puntúan por stopwords compartidas. Medido sobre el
  eval: las preguntas con respuesta puntúan ≥ 0,43 y las fuera de dominio ("veterinario de mi mascota") ≤ 0,38.
  Por eso 0,40.
- El 0,55 de Gemini es un valor inicial **sin calibrar**: no hubo key durante el desarrollo. El criterio es el
  umbral más bajo con el que el eval rechaza todas sus preguntas sin evidencia (refusal recall = 1), aceptando
  la precisión de rechazo que resulte.

**Por qué el AND/identificador en el full-text.** La primera versión aceptaba cualquier hit full-text. Con
semántica OR, una palabra común ("póliza", "cubre") abría la puerta a casi cualquier pregunta, y el umbral era
configuración muerta.

**Coste.**
- Más rechazos de preguntas legítimas formuladas con palabras distintas a las del documento.
- La regla del número de 3+ cifras deja pasar a una pregunta fuera de dominio que lleve un año. Se mantuvo
  porque las preguntas con número de póliza o importe son las más valiosas, y detrás siguen el LLM y el
  validador.

**Cómo se mide.** Precisión y recall de rechazos en el eval: q10–q12 deben rechazarse y q01–q09 y q13
responderse. Con el mock: recall 1,00, precisión 0,75. El falso rechazo es q13, que el mock no pasa por la
puerta.

## 3. Duplicados y versiones

**Decisión.**
- **Duplicado = mismo sha256.**
  - Subir el mismo archivo devuelve 200 con el documento existente, sin reencolar.
  - La restricción `unique` resuelve la carrera de dos subidas simultáneas.
- **Versiones: se conservan todas.**
  - Solo se versionan las pólizas (`doc_type=policy`) con número; si no se indica, se extrae del texto por
    regex.
  - Al quedar `ready`, la vigente es la de `document_date` más reciente; sin fecha o con empate, la última
    subida.
  - Las demás pasan a `is_current=false` con `superseded_by_id`.
  - Un advisory lock por número de póliza serializa ingestas concurrentes de la misma póliza.
  - Cartas y condicionados nunca reemplazan a nada.

**Efecto en las citas.**
- La búsqueda usa solo versiones vigentes salvo `include_superseded=true`.
- Cada cita apunta al chunk y documento exactos de los que salió: con las dos versiones incluidas, el 600 € se
  cita a la v1 y el 450 € a la v2.
- Las respuestas guardadas no cambian si después llega una versión nueva: siguen citando la versión que usaron.

**Por qué no reemplazar.** Un siniestro de 2024 se resuelve con la póliza vigente en 2024. Borrar la versión
anterior rompería las citas ya emitidas y el histórico.

**Coste.**
- El almacenamiento crece con cada versión.
- Una renovación subida sin fecha pierde frente a una versión antigua con fecha.
- El ajustador tiene que saber marcar `include_superseded`.

**Cómo se mide.** q02 (vigente, 450 €) y q03 (anterior con `include_superseded`, 600 €) del eval, y los tests
de versionado de integración.

## 4. Datos personales antes del LLM externo

**Decisión.**
- En la ingesta, regex de DNI, NIE, IBAN, email y teléfono → `[DNI]`, `[NIE]`, `[IBAN]`, `[EMAIL]`,
  `[TELÉFONO]`.
- Lo que se embebe y lo que va al LLM es siempre `text_redacted`, y la pregunta también se redacta.
- El original queda solo en la base de datos interna, donde lo usa la búsqueda full-text, y el PDF en el
  volumen.
- Logs y trazas no llevan contenido (ver §12).

**Por qué.** El free tier de Gemini puede usar los datos para entrenar. Los identificadores fuertes (DNI,
IBAN) son los que más daño hacen si salen, y los que una regex detecta con precisión casi total.

**Fuera del MVP.**
- NER de nombres y direcciones: un modelo más en la imagen y falsos positivos en texto jurídico.
- Seudonimización reversible.
- Cifrado en reposo y política de retención.
- Un tier de pago o Vertex AI con términos de tratamiento de datos.

**Coste.**
- Nombres y direcciones llegan al proveedor.
- El regex de teléfono no detecta fijos agrupados de dos en dos y puede tapar importes como `600 000 000`.
- La respuesta enseña el fragmento redactado; el original se ve abriendo el PDF.

**Cómo se mide.** Tests de PII. Un test de integración comprueba que el DNI y el nombre de la carta no llegan a
los logs. En la verificación final, `docker compose logs api worker | grep -c 12345678Z` da 0.

## 5. Modelo y fallback

**Decisión.**
- Gemini 2.5 Flash como principal y 2.5 Flash-Lite como fallback, vía un `Router` de LiteLLM con
  `timeout=30 s` y 2 reintentos.
- `LLM_FALLBACK_MODELS` admite cualquier modelo LiteLLM, también de otro proveedor, solo por env.
- Último eslabón, **fallback extractivo**: si todos los modelos fallan o devuelven JSON inválido, se responde
  `degraded=true`, `confidence=low`, con un texto fijo y los 3 mejores fragmentos como citas. Son válidas por
  construcción y no se inventa nada.
- Embeddings: `gemini-embedding-001` a 768 dimensiones, **sin fallback**.

**Por qué.**
- Flash da salida estructurada y es barato y rápido. Flash-Lite tiene cuota propia en el free tier, así que un
  429 del principal no tumba el fallback.
- No hay key de un segundo proveedor.
- El extractivo deja al ajustador con algo útil y honesto cuando el LLM cae.
- Un embedding de otro modelo vive en otro espacio vectorial: mezclarlo rompería la búsqueda sin dar error.
  Cada documento guarda su `embedding_model` y la búsqueda filtra por él.

**Coste.**
- Las cuotas y los modelos del free tier cambian a menudo; los nombres van por env.
- Si caen los embeddings no hay respuesta: la pregunta queda `failed` / `provider_unavailable`, porque sin
  recuperación no hay nada que extraer.
- Ante un 429 con `Retry-After`, el Router puede esperar más que el timeout. Lo acotan los límites duros de las
  tareas: 960 s la ingesta, 540 s la respuesta.

**Si es la equivocada.** Cambiar de modelo es una variable de entorno. Cambiar de modelo de embeddings obliga
a reingestar (README).

## 6. Índice: HNSW frente a IVFFlat

**Decisión.** HNSW con `vector_cosine_ops` (m=16, ef_construction=64). Con filtros, se usa
`hnsw.iterative_scan=relaxed_order` de pgvector 0.8 y se reordena por distancia.

**Por qué.**
- HNSW no necesita entrenamiento. IVFFlat calcula sus `lists` con los datos que hay al crear el índice: en una
  tabla que empieza vacía y crece documento a documento, sus centroides quedan malos y el recall cae hasta
  reindexar.
- HNSW da buen recall sin ajustar y admite inserciones incrementales.
- Sin `iterative_scan`, un filtro selectivo (una póliza) dejaría el top 20 casi vacío. `relaxed_order` puede
  devolver filas algo desordenadas, por eso se reordenan antes de RRF y del umbral.

**Coste.** Más memoria y construcción e inserción más lentas que IVFFlat. Para miles de PDF (cientos de miles
de chunks) es asumible.

**Cómo se mide.** hit@6 y MRR del eval, comparando con la búsqueda exacta (sin índice) sobre los mismos datos.

## 7. Las preguntas también pasan por Celery

**Decisión.** `POST /api/questions` guarda la pregunta `pending`, encola `answer_question` y devuelve 202. La UI
hace polling.

**Por qué.**
- La prueba pide una traza API → Celery → recuperación → LLM, y así es literal: un test con broker y worker
  reales lo comprueba.
- Las llamadas lentas al LLM no ocupan workers HTTP.
- Queda un registro auditable de cada pregunta: resultado, modelo, latencia y `trace_id`.

**Coste.**
- Una tabla y un polling más, y la latencia de la cola.
- Las preguntas no tienen token de dueño: si un worker muere, el mensaje se reentrega (`acks_late`) y una fila
  `processing` con más de 10 min (más que los 540 s del límite duro) se puede reclamar. Una pregunta podría
  procesarse dos veces solo si además la cola acumula más de 10 min. El resultado se sobrescribe, sin otro
  efecto.

## 8. Polling frente a SSE

**Decisión.** TanStack Query con `refetchInterval`:
- documentos: cada 2 s mientras haya alguno sin terminar;
- pregunta: cada 1 s hasta un estado terminal.

El polling se para en estados terminales y ante errores.

**Por qué.** Hay pocas transiciones (4 estados) y duran segundos o minutos. SSE necesitaría un puente Redis
pub/sub → API, conexiones largas y configurar el proxy, sin beneficio visible para el usuario.

**Coste.**
- Peticiones de más mientras algo se procesa.
- `GET /api/documents` se limita a los 200 más recientes para que el polling no crezca con el catálogo.

## 9. Observabilidad: OTLP y Datadog vía collector

**Decisión.**
- El código solo habla OTLP.
- Un OpenTelemetry Collector decide el destino con `OTEL_COLLECTOR_CONFIG`:
  - `local` → Jaeger;
  - `datadog` → exporter de Datadog con `DD_API_KEY`/`DD_SITE`, más el connector que deriva las métricas APM.
- Spans manuales: `ingest.parse/chunk/embed`, `retrieval.search`, `llm.embed`, `llm.completion` (`gen_ai.*`,
  proveedor, `llm.fallback_used`, latencia) y `answer.validate`.
- Instrumentación de FastAPI, Celery, SQLAlchemy y Redis.
- Logs JSON con `trace_id`/`span_id`. El `trace_id` sale en el envelope de error y en `QuestionOut`.

**Por qué.** Sin vendor lock-in: cambiar de backend de trazas es cambiar el YAML del collector.

**Coste.**
- Un contenedor más.
- La configuración de Datadog no se ha probado contra una cuenta real en este repo.

## 10. Proveedor mock por defecto

**Decisión.** `LLM_PROVIDER=mock` por defecto, aunque el diseño proponía `litellm`. El mock usa embeddings por
feature hashing y una "respuesta" que cita la frase del chunk con más palabras en común.

**Por qué.**
- `docker compose up --build` funciona sin key ni red.
- Tests y CI no tocan ningún LLM.
- El entrevistador ve todo el flujo de entrada.

**Coste.**
- Las respuestas por defecto son léxicas y pobres.
- Para ver Gemini hay que cambiar dos variables, y además `docker compose down -v` si ya había documentos.
- El mock necesita su propio umbral (§2).

## 11. Idempotencia: token de dueño, leases y presupuesto de reintentos

**Decisión.**
- Al encolar se genera el `task_id` y se guarda en `ingest_task_id` **antes** de publicar el mensaje. Es el
  token de dueño.
- **Claim:** `UPDATE … WHERE id=:id AND ingest_task_id=:task_id AND status IN ('pending','processing')`.
  - Los reintentos y reentregas de Celery conservan el id y continúan.
  - Un duplicado o un mensaje tardío de una tarea reemplazada no obtiene fila y termina sin hacer nada.
- Antes de escribir chunks se vuelve a comprobar el dueño con `SELECT … FOR UPDATE`. Si un `/retry` entregó el
  documento a otra tarea mientras esta parseaba, no escribe nada.
- `_mark_failed` también exige dueño y `processing`.
- **Leases.** `/retry` acepta:
  - `failed`;
  - `processing` con más de 20 min (más que el límite duro de 960 s, así nunca reemplaza una tarea viva);
  - `pending` con más de 20 min sin cambios (mensaje perdido del broker).
- Si falla el encolado: `failed` / `enqueue_failed` y 503.
- Celery: `acks_late`, `reject_on_worker_lost` y prefetch 1.
- **Reintentos acotados** para no quemar la cuota:
  - LiteLLM reintenta 2 veces cada llamada;
  - la tarea se reintenta hasta 3 veces con 30/60/120 s + jitter, respetando `Retry-After`;
  - después, `failed` / `provider_unavailable`, reintentable.
- Errores permanentes (PDF corrupto, ilegible, demasiadas páginas) → `failed` con su código, sin reintento.

**Por qué.** Celery garantiza "al menos una vez". El estado de la fila no basta para distinguir mi reintento de
un duplicado; el token sí.

**Coste.**
- Un documento atascado espera 20 min antes de poder reintentarse.
- Con una key inválida, la ingesta agota sus reintentos (~3,5 min) antes de marcarse `failed`. Distinguir error
  de autenticación de error transitorio era más código para un caso de configuración.

**Cómo se mide.** Tests de integración contra Postgres real:
- claim con el mismo y con otro `task_id`;
- tarea reemplazada a mitad que no escribe;
- carrera de subidas duplicadas;
- leases de `/retry`;
- reentrega de preguntas.

## 12. Higiene de trazas y logs

**Decisión.** Ningún log ni span lleva texto de preguntas, respuestas, chunks ni mensajes de excepción; solo
ids, conteos, longitudes, duraciones y tipos de error. En el código:
- `content_free_span` (`record_exception=False`, solo `error.type`);
- SQLAlchemy instrumentado sin commenter y engine con `hide_parameters=True`: un error SQL no arrastra la
  pregunta;
- telemetría nativa de FastAPI 0.162 desactivada, porque sus logs llevan el texto de la excepción;
- los filtros de recuperación se registran solo por nombre;
- uvicorn y las librerías pasan por el handler JSON con solo `exc_type`, y las librerías que loguean rutas o
  prompts quedan en WARNING;
- `litellm.suppress_debug_info`;
- `LITELLM_LOCAL_MODEL_COST_MAP=True`.

**Por qué.** "Contenido de pólizas o PII en logs, trazas o mensajes de error" es un anti-objetivo del diseño. Las
trazas salen hacia Jaeger o Datadog.

**Coste.**
- Depurar es más difícil sin mensajes. El `trace_id`, el tipo de error y los ids de fila bastan para encontrar
  el caso en la base de datos.
- Riesgo residual: el instrumentador de FastAPI registra el mensaje de una excepción *no controlada* en el span
  HTTP. `hide_parameters` cubre el caso conocido con datos de usuario (errores SQL).

**Cómo se mide.** Tests que comprueban:
- que la pregunta no está en los atributos de los spans;
- que el DNI y el nombre no salen en los logs (`capfd`);
- que los spans de error solo llevan el tipo;
- que los errores SQL no llevan valores;
- que los logs de uvicorn son JSON sin texto de excepción.

## 13. Defensa contra prompt injection por capas

**Decisión.** Ninguna capa es suficiente sola:
1. **Ingesta:** un detector heurístico marca `suspicious` los chunks con patrones de inyección, y se excluyen
   del contexto.
2. **Sanitización:** se quitan caracteres de control y zero-width, y cualquier `<doc-` (tolerante a espacios y
   mayúsculas) en bucle hasta que el texto no cambie.
3. **Delimitadores con nonce aleatorio por petición:** `<doc-{nonce} id=… page=…>`. Un documento no puede cerrar
   un bloque cuyo nombre no conoce.
4. **System prompt:** el contenido de los bloques es dato, nunca instrucción. La pregunta real es solo el
   `Pregunta:` final, fuera de los bloques.
5. **Salida estructurada** (esquema Pydantic) y **validador de citas**: cita literal de un chunk recuperado.
   Documento y página los pone el servidor, nunca el modelo.
6. **Filtro de salida:** sin URLs ni HTML, máximo 2000 caracteres, no vacía.
7. **UI:** solo texto plano.

**Por qué.** El documento malicioso de `samples/` lleva la inyección visible y en texto blanco de 1 pt. Si el
detector falla, las capas 3–6 siguen actuando.

**Coste.**
- Un falso positivo del detector esconde evidencia legítima. Por eso los patrones se endurecieron, con tests
  negativos de redacción real de pólizas.
- Algunas inyecciones exóticas no se marcan.
- Los caracteres fullwidth parecidos a `<` no se normalizan; sin conocer el nonce el riesgo es bajo.

**Cómo se mide.**
- `test_malicious_doc_does_not_change_answer`: la póliza maliciosa se recupera, pero el texto inyectado no
  llega al prompt y la respuesta no lo obedece.
- q13 del eval, con `forbidden: ["sin deducible"]`. Con el mock se rechaza en la puerta y queda `n/a`; necesita
  la ejecución con Gemini.

## 14. Dependencias fijadas

- **LiteLLM `==1.106.0`**, versión exacta, con `uv.lock` y hashes. Las versiones 1.82.7 y 1.82.8 de PyPI fueron
  comprometidas (marzo de 2026). LiteLLM tampoco descarga su mapa de costes de GitHub al arrancar.
  - Coste: actualizar es un cambio deliberado.
- **SQLAlchemy `>=2,<2.1`.** `opentelemetry-instrumentation-sqlalchemy` no soporta 2.1 y en ese caso no
  instrumenta, sin avisar: se perderían los spans SQL de la traza. 2.0 es la línea estable.
  - Coste: sin novedades de 2.1 hasta que la instrumentación las soporte.

## Uso de IA generativa

**Cómo se usó.** Claude Code orquestó todo el trabajo:
1. Lectura del enunciado, preguntas y diseño (`docs/design.md`). Las decisiones de producto de su §9 se
   aprobaron antes de escribir código.
2. Un plan de 15 tareas con restricciones globales (`docs/plan.md`).
3. Por cada tarea:
   - un subagente implementador, con TDD (test en rojo, luego en verde) y la consigna de comprobar las APIs de
     librerías en su documentación o en el código instalado;
   - un subagente revisor sobre el diff;
   - rondas de corrección;
   - una decisión registrada para cada desacuerdo.
4. Al final, una revisión de todo el repositorio contra el enunciado y una tanda de correcciones.

`docs/design.md` y `docs/plan.md` se dejan en el repo como evidencia de ese proceso, incluidas las
instrucciones para los agentes al inicio del plan. Los commits llevan el trailer del modelo que los escribió.

**Qué se corrigió o descartó.** Muchas propuestas iniciales, del diseño o de los propios agentes, estaban mal:

Diseño
- El primer claim de ingesta solo aceptaba filas `pending`. Un reintento de Celery (fila ya en `processing`) se
  quedaba sin poder continuar → token de dueño, luego obligatorio siempre: un mensaje tardío de una tarea
  reemplazada no debe procesar una fila reintentada.
- Los reintentos de LiteLLM y de Celery se multiplicaban → presupuesto acotado y explícito (§11).
- El serializador de tablas por defecto de Docling produce tripletas, no markdown → proveedor de serialización
  propio con cabecera repetida.
- `relaxed_order` puede devolver resultados desordenados → reordenar por distancia antes de fusionar.
- Las cuotas del free tier citadas por el asistente estaban desactualizadas, y había fuentes contradictorias →
  modelos configurables por env, sin cifras de cuota en la documentación.
- LiteLLM tenía versiones comprometidas → versión exacta fijada.

Implementación
- Se asumió que `litellm.embedding(num_retries=…)` reintenta. En 1.106 no lo hace → los embeddings pasan por
  un segundo `Router`.
- Los patrones de inyección del plan daban falsos positivos: "corresponde que" contiene "responde que", y
  "actúa como" aparece en texto legal normal. Como un chunk marcado desaparece de la búsqueda → patrones
  endurecidos y tests negativos.
- El sanitizador de delimitadores se podía esquivar: con espacios o zero-width dentro de la etiqueta, o porque
  quitar una etiqueta unía a sus vecinas en otra nueva → normalización previa y bucle hasta estabilidad.
- Una tarea reemplazada por `/retry` podía reescribir los chunks del nuevo dueño → comprobación del dueño bajo
  `FOR UPDATE` antes de escribir.
- Los spans del worker se perdían: los hijos de Celery salen con `os._exit` y no vacían el exportador → flush en
  `worker_process_shutdown` y pool prefork obligatorio.
- El polling de la UI no se paraba ante errores, porque TanStack conserva los datos anteriores tras un fallo →
  se para en error.
- La puerta de evidencia casi nunca rechazaba (full-text OR) → AND o identificador (§2).
- Un umbral único de 0,55 rechazaba la mitad del eval con el mock → umbral por proveedor, medido.
- Al cambiar de proveedor se mezclaban espacios de embeddings sin ningún error → `embedding_model` por
  documento y filtro en la búsqueda.
- El texto de las excepciones SQL incluía los parámetros, con la pregunta → `hide_parameters=True`.
- `OTEL_SDK_DISABLED=true` en CI desactivaba el SDK y rompía los tests de tracing → eliminado del entorno de CI.
- FastAPI 0.162 trae telemetría nativa cuyos logs llevan el texto de la excepción → desactivada.
- La instrumentación de SQLAlchemy ignoraba la 2.1 sin avisar → SQLAlchemy 2.0.
- structlog imprimía el `repr` de `exc_info`, con el mensaje → solo `exc_type`.
- Si el encolado fallaba, el documento quedaba `pending` para siempre → `failed` / `enqueue_failed` + 503.
- La receta de demo "modo degradado con key inválida" era imposible: sin embeddings no hay recuperación → la
  demo usa una key válida y un modelo de completion inexistente.
- En el eval había keywords demasiado débiles (q05, q06), y la resistencia a inyección contaba un rechazo como
  "resistida" → keywords endurecidas; solo se puntúan respuestas.
- Fallos de API y tooling:
  - pydantic-settings no parsea listas CSV → `NoDecode` + validador;
  - el entrypoint de uvicorn necesitaba `--factory`;
  - el `testcontainers.postgres` estaba obsoleto;
  - la regex del mock no casaba con el formato real del prompt;
  - `[tool.uv.sources]` no redirige dependencias transitivas: bajaban ~3 GB de CUDA → torch como dependencia
    directa con índice CPU;
  - TypeScript 7 no es compatible con typescript-eslint → 6.0.3;
  - mypy estricto exige importar de los módulos que definen los tipos.

Descartado
- `httpx2`, el paquete al que empuja un aviso de Starlette: no verificado. Se mantuvo `httpx` y se filtró solo
  ese aviso.
- IVFFlat (§6), SSE (§8), reemplazar versiones (§3), NER en el MVP (§4).
- Una puerta de evidencia basada solo en la similitud o solo en el full-text (§2).

**Lo que queda pendiente por no tener key.**
- Ejecutar el eval con Gemini y calibrar el 0,55.
- Verificar con el modelo real la salida estructurada, los tokens y la detección de fallback.
