# Docta — Estado actual

**Revisión documental:** 2026-09-09\
**Base inspeccionada:** `develop`, merge `e31d1aa` (PR #3 desde `feature/walking-skeleton`).\
**Estado:** Fase 0 completada. Fase 1 planificada; Incremento 5 pendiente de implementación.

Esta página describe el código inspeccionado y la evidencia registrada; no afirma que servicios
locales estén activos ni que los tests históricos se hayan vuelto a ejecutar en esta revisión.
El [plan activo](PHASE_1_TUTOR_QUALITY.md) define qué construir a continuación.

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
| UI docente/estudiante | Next.js, React, TypeScript, assistant-ui y CSS propio. BFF, PKCE, cookie cifrada HttpOnly, publicación, chat durable, fragmentos citados y estados visibles. | [Workspace](../apps/web/components/workspace.tsx), [ADR 0003](adr/0003-browser-workspace-and-oidc-session.md), [E2E](../apps/web/e2e/workspace.spec.ts). |
| Infraestructura local | PostgreSQL `16.10-alpine` sin pgvector, Redis y MinIO en Compose; worker opcional en contenedor. API y web se ejecutan con los comandos del README. Migraciones hasta 0008. | [Compose](../infra/compose.yaml), [migraciones](../apps/api/migrations/versions), [rebuild test](../tests/integration/test_local_dependencies.py). |

## Baseline para los próximos experimentos

- Retrieval: `spanish-fts-and-top5-v1`; recibe únicamente la pregunta original. La condición
  `Message.question == question` es parte de la validación del request autorizado.
- Prompt: `phase0-guidance-v2`; permite explicaciones conceptuales breves y guía para ejercicios.
  Estos identificadores siguen vigentes; cambiar la fase documental no cambia las versiones del runtime.
- El generador recibe hasta seis mensajes completados y 12.000 caracteres de historial. Ese historial
  no se usa hoy para resolver una consulta antes del retrieval.
- Adapter real Chat Completions con salida estructurada, perfiles explícitos `llama_cpp`/`unsloth`
  y sin retry automático. No hay proveedor por defecto ni gateway desplegado.
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

El siguiente paso es medir este baseline con 30–50 casos revisados, antes de modificar retrieval
o escoger un modelo mayor. Véase [Incremento 5](PHASE_1_TUTOR_QUALITY.md#incremento-5--evaluation-harness--dataset-v0).
