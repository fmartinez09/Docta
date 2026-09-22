# Docta — Estado actual

**Revisión documental:** 2026-09-22\
**Base inspeccionada:** HEAD `8ee13a5` (UI), posterior a `0171c2a` (documentación) y al merge
`e31d1aa` de Fase 0. Inspección de código y configuración versionada, no del despliegue activo.\
**Estado:** Fase 0 completada. Fase 1 planificada; Incremento 5 pendiente de implementación.

Esta página describe el código inspeccionado y la evidencia registrada; no afirma que servicios
locales estén activos ni que los tests históricos se hayan vuelto a ejecutar en esta revisión.
El [plan activo](PHASE_1_TUTOR_QUALITY.md) define qué construir a continuación.
La revisión anterior se basaba en `e31d1aa` el 2026-09-09; se conserva esa procedencia en
el [registro de decisiones](DECISIONS.md). El cambio local preexistente en
`apps/web/next-env.d.ts` no se modifica ni se presenta como una entrega de esta revisión.

## Flujo construido

```text
OIDC/PKCE → sesión web → membresía autorizada → curso
  → upload directo a MinIO → outbox/Redis Streams → worker → PDF digital/FTS
  → activación atómica del corpus
  → pregunta persistida + corpus capturado → retrieval autorizado
  → generación o abstención → validación → respuesta/citas persistidas → SSE/UI
```

| Capacidad | Implementación y alcance | Evidencia en el repositorio |
| --- | --- | --- |
| Identidad y cursos | JWT OIDC verificado en API; membresías locales; docente creador. | [Identidad](../apps/api/src/docta_api/identity.py), [tests de cursos](../tests/integration/test_courses.py). |
| Upload y procedencia | Presigned PUT S3, versión de objeto inmutable, SHA-256, PyMuPDF y chunks por página limitados por caracteres. Rechazo explícito de PDF inválido, cifrado o sin texto. | [Parser](../apps/api/src/docta_api/pymupdf_parser.py), [chunking](../apps/api/src/docta_api/document_parser.py), [tests documentales](../tests/integration/test_documents.py). |
| Ingesta durable | Outbox PostgreSQL + Redis Streams; relay y consumer en el worker; leases, fencing, reintentos acotados y recuperación. | [Worker](../apps/api/src/docta_api/worker.py), [tests de jobs](../tests/integration/test_jobs.py), [ADR 0001](adr/0001-ingestion-durability-and-response-delivery.md). |
| Publicación | Corpus inmutable `READY`, activación compare-and-swap; cada pregunta captura una versión. El flujo actual publica una versión documental por corpus. | [Documentos](../apps/api/src/docta_api/documents.py), [tests documentales](../tests/integration/test_documents.py). |
| Retrieval | `plainto_tsquery("spanish", question)`, AND entre términos no descartados, ranking `ts_rank_cd`, desempate por ordinal y máximo cinco chunks. Revalida mensaje, curso, corpus, propietario y membresía. | [PostgresRetriever](../apps/api/src/docta_api/postgres_retriever.py), [tests de conversaciones](../tests/integration/test_conversations.py). |
| Conversación | Una fila `Message` agrupa pregunta y respuesta; `pending → completed/failed`, idempotencia exacta, evidencia durable antes del modelo y respuesta/citas atómicas. Ejecución en API independiente del socket, con deadline y recuperación de fallos. | [Servicio](../apps/api/src/docta_api/conversations.py), [runtime](../apps/api/src/docta_api/conversation_runtime.py), [ADR 0002](adr/0002-durable-conversation-and-scoped-rag.md). |
| Tutor y validación | `hint`, `guided_question`, `explanation`, `abstain`; esquema, límites, citas a evidencia recuperada y citas textuales exactas. Sin evidencia se abstiene sin invocar modelo. | [Contratos RAG](../apps/api/src/docta_api/rag.py), [adapter HTTP](../apps/api/src/docta_api/tutor_http.py), [tests del tutor](../tests/unit/test_tutor_http.py). |
| UI docente/estudiante | Next.js, React, TypeScript, assistant-ui, CSS propio, Radix Dialog y Lucide. BFF, PKCE, cookie cifrada HttpOnly, publicación, chat durable, fragmentos citados y estados visibles. HEAD actualiza componentes/estilos sin añadir planner ni herramientas. | [Workspace](../apps/web/components/workspace.tsx), [dependencias](../apps/web/package.json), [ADR 0003](adr/0003-browser-workspace-and-oidc-session.md), [E2E](../apps/web/e2e/workspace.spec.ts). |
| Infraestructura local | PostgreSQL `16.10-alpine` sin pgvector, Redis y MinIO en Compose; worker opcional en contenedor. API y web se ejecutan con los comandos del README. Migraciones hasta 0008. | [Compose](../infra/compose.yaml), [migraciones](../apps/api/migrations/versions), [rebuild test](../tests/integration/test_local_dependencies.py). |

## Baseline para los próximos experimentos

- Retrieval: `spanish-fts-and-top5-v1`; recibe únicamente la pregunta original. La condición
  `Message.question == question` es parte de la validación del request autorizado.
- Prompt: `phase0-guidance-v2`; permite explicaciones conceptuales breves y guía para ejercicios.
  Estos identificadores siguen vigentes; cambiar la fase documental no cambia las versiones del runtime.
- El generador recibe hasta seis mensajes completados y 12.000 caracteres de historial. Ese historial
  no se usa hoy para resolver una consulta antes del retrieval.
- Adapter real Chat Completions con salida estructurada, perfiles explícitos `llama_cpp`/`unsloth`
  y sin retry automático. No hay proveedor por defecto ni gateway gestionado por el Compose
  versionado. Esto no afirma que el usuario carezca de un gateway externo en su entorno local.
- Qwen3.5-9B/Unsloth tiene evidencia local de compatibilidad; no es una selección de modelo de piloto.
  ZITADEL es la configuración local documentada, no una elección de proveedor de producción cerrada.

## Evidencia histórica y sus límites

| Registro | Qué demuestra | Qué no demuestra |
| --- | --- | --- |
| [Cierre del Incremento 4](WALKING_SKELETON.md), 2026-09-08 | Suite Python, browser E2E, tests web, lint y build registrados como correctos; flujo con infraestructura real y dobles de OIDC/modelo en tests. | Calidad general del modelo o despliegue de piloto. |
| [Diagnóstico posterior del prompt v2](runbooks/conversations.md), 2026-09-08 | Registro más reciente: 118 tests Python incluyendo browser, diez tests web, Ruff, ESLint y diff check; explicaciones con citas exactas en dos replays locales. | Benchmark representativo, soporte semántico de cada afirmación o mejora del aprendizaje. |
| [Smoke de ADR 0003](adr/0003-browser-workspace-and-oidc-session.md) | Una solicitud sintética Qwen3.5-9B validada en 5,6 segundos. | Latencia p95, fiabilidad del proveedor o validación browser de producción con modelo real. |

Los conteos corresponden a verificaciones sucesivas; se preservan sin sobrescribir resultados
anteriores. El cierre de Fase 0 acredita el flujo técnico. La preparación del piloto requiere
evaluación de calidad, operación, privacidad y restauración de backups fuera del host.

## Correspondencia con la propuesta de harness

| Propuesta | Realidad inspeccionada | Pendiente |
| --- | --- | --- |
| Extraer `RetrievalPort` | `Retriever` ya devuelve `Evidence`; búsqueda separada de generación. | Evolucionar consulta/scope y resultados sin perder garantías. |
| `TutorRuntime` | `ConversationRuntime` implementa la secuencia fija. | Tools, decisiones locales y presupuestos si se aprueba el cambio. |
| `TutorActivity` / `PolicyResolver` | Historial acotado y política en prompt; no entidades pedagógicas estructuradas. | Contrato, persistencia y revisión docente. |
| `ResponseGate` | `EvidenceResponseValidator` verifica esquema, alcance y citas exactas. | Evaluar soporte semántico y pertinencia; no prometer una garantía perfecta. |
| Commit / recuperación | Pregunta durable, estado condicional, respuesta/citas atómicas y reconciliación por deadline. | Reanudación entre pasos e intentos/cancelación, no presentes. |
| Harness de evaluación | Tests técnicos disponibles. | Runner de calidad, dataset revisado y baseline medido. |

La [arquitectura del harness](DOCTA_HARNESS_ARCHITECTURE.md) es diseño reconciliado, no evidencia
de implementación. ADR 0004 sigue excluyendo agent loops; las alternativas están en D-05/D-06
del [registro de decisiones](DECISIONS.md).

## Límites vigentes

- No hay dataset dorado ni harness de evaluación de calidad; los tests con fakes no los sustituyen.
- No hay resolución conversacional, embeddings, pgvector, RRF, reranker, planner pedagógico
  estructurado, `TutorResponse v2` ni OpenUI. Los contratos de arquitectura correspondientes son diseños objetivo.
- Validar una cita prueba procedencia y literalidad, no que todas las afirmaciones estén respaldadas.
- El caso «¿Y una derivada?» es un candidato de evaluación. La falta de resolución existe en el código,
  pero la causa de esa abstención concreta requiere inspeccionar corpus capturado, evidencia y salida.
  Una reescritura no garantiza mejorar FTS; puede producir los mismos términos o introducir restricciones adicionales.
- No hay inscripción desde UI, acceso docente a conversaciones ajenas, visor PDF integrado ni feedback.
  Las listas de cursos/documentos/conversaciones están limitadas a los 100 registros más recientes;
  el historial de mensajes sí tiene paginación.
- La sesión vence como máximo en una hora; no hay refresh token. Un borrador no aceptado se pierde al recargar.
- No se han establecido aquí CI/CD, cuotas, ledger de costes, despliegue de producción ni restauración
  de backups de piloto. El rebuild de migraciones en tests no equivale a esa restauración.

## Verificación de esta revisión documental

Se inspeccionaron fuentes, manifiestos, Compose, ADR, runbooks, historial Git y rutas documentales.
Verificaciones ejecutadas el 2026-09-22:

| Comprobación | Resultado y alcance |
| --- | --- |
| Validador documental ad hoc con Node.js | 21 documentos, 168 enlaces locales y sus anclas: sin errores. 20 enlaces externos detectados, no verificados en esta tarea. |
| Rutas literales del repositorio | 17 referencias entre documentos no archivados: existen. No pretende resolver todos los ejemplos de código de las referencias históricas. |
| Comparación con `git show HEAD:<ruta>` y original importado | Cuerpos originales de ambas arquitecturas preservados; ADR 0001–0004 conservados con notas añadidas; walking skeleton preservado salvo nota explícita. |
| Checklists de Fase 0 / Fase 1 frente a HEAD | Sin cambios en estados ni criterios marcados. |
| `git diff --check` | Correcto. Advertencias de normalización LF/CRLF no son fallos de contenido. Los archivos nuevos se revisan también en la validación documental. |

No se ejecutaron suites Python/web, migraciones, servicios ni llamadas al modelo; los resultados
de tests de septiembre 7–8 siguen siendo históricos. No se verificó exhaustivamente la bibliografía
externa ni el despliegue del usuario. El cuerpo importado del harness se comparó con su copia
original, normalizando finales de línea; no se incorporaron adjuntos privados adicionales.

El siguiente paso es medir este baseline con 30–50 casos revisados, antes de modificar retrieval
o escoger un modelo mayor. Véase [Incremento 5](PHASE_1_TUTOR_QUALITY.md#incremento-5--evaluation-harness--dataset-v0).
