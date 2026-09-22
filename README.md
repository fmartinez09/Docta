# Docta

Tutor pedagógico RAG basado en evidencia del curso. Monolito modular con Next.js, FastAPI,
PostgreSQL, almacenamiento S3/MinIO y worker de ingesta con Redis Streams.

**Estado revisado documentalmente: 2026-09-22, código base `8ee13a5`.**
Fase 0 completada: PDF digital → indexación → publicación → pregunta durable → respuesta
validada con citas → interfaz OIDC. Esto demuestra el recorrido técnico, no calidad pedagógica
ni preparación para un piloto.

El próximo trabajo es **Incremento 5: harness de evaluación y dataset v0 revisado**.
No está implementado. El harness pedagógico adaptativo es una propuesta diferente, aún pendiente
de decisión; no se ha adoptado un agent loop ni un nuevo framework.

## Por dónde empezar

| Pregunta | Documento |
| --- | --- |
| ¿Qué está construido y con qué límites? | [Estado actual](docs/CURRENT_STATE.md) |
| ¿Qué se decidió y qué falta decidir? | [Registro de decisiones](docs/DECISIONS.md) y [ADR](docs/adr/README.md) |
| ¿Qué construimos a continuación? | [Fase 1 y criterios de Incremento 5](docs/PHASE_1_TUTOR_QUALITY.md) |
| ¿Cómo se organiza el sistema? | [Arquitectura](docs/DOCTA_ARCHITECTURE.md) |
| ¿Qué significa harness y qué cambiaría? | [Evaluación y runtime pedagógico](docs/DOCTA_HARNESS_ARCHITECTURE.md) |
| ¿Cómo instalar, operar y diagnosticar? | [Runbooks](docs/runbooks/README.md) |
| ¿Dónde está la historia? | [Índice documental](docs/README.md) y [Fase 0](docs/WALKING_SKELETON.md) |

## Arranque local

Requisitos del proyecto: Node.js 22, npm 10, Python 3.12/3.13, uv y Docker Compose v2.
Desde la raíz, conservar cualquier `.env` existente:

En una instalación existente, detener API y workers antes de migrar y consultar el
[procedimiento de actualización](docs/runbooks/ingestion.md#migrations-and-verification).

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
npm ci
uv sync --cache-dir .uv-cache
docker compose --env-file .env -f infra/compose.yaml up -d --wait
uv run --cache-dir .uv-cache alembic -c apps/api/alembic.ini upgrade head
```

Configurar OIDC, origen/callback web y secreto de sesión según el
[runbook del workspace](docs/runbooks/browser-workspace.md). Para respuestas reales, configurar
endpoint, modelo y credencial conjuntamente según el [runbook del tutor](docs/runbooks/conversations.md).
Sin OIDC no hay acceso autenticado; sin proveedor no hay generación real cuando la evidencia la requiere.

Iniciar cada proceso en una terminal distinta:

```powershell
uv run --cache-dir .uv-cache uvicorn docta_api.main:app --app-dir apps/api/src --env-file .env --host 127.0.0.1 --port 8000
```

```powershell
uv run --cache-dir .uv-cache python -m docta_api.worker
```

```powershell
npm run dev:web
```

El origen predeterminado es `http://127.0.0.1:3000`. Para un entorno configurado en 3100,
usar `npm run dev:web:local` y mantener callback, CORS y `DOCTA_WEB_ORIGIN` sincronizados.
La [guía completa de desarrollo](docs/runbooks/local-development.md) incluye worker Docker,
helpers de token/upload, endpoints y comprobaciones de salud. No hay proveedor/modelo por defecto.

## Verificación

Checks sin servicios externos:

```powershell
uv run --cache-dir .uv-cache pytest tests/unit
uv run --cache-dir .uv-cache ruff check apps/api/src scripts tests
npm run lint:web
npm run test:web
npm run build:web
git diff --check
```

Integración y E2E requieren servicios de prueba, imagen worker y navegador; seguir la
[secuencia completa](docs/runbooks/local-development.md#clean-checkout-to-green-checks).
Usan bases, buckets y streams generados, nunca los de desarrollo. Ejecutar suites serialmente:
algunos tests reinician dependencias de prueba. No borrar volúmenes de desarrollo para hacerlas pasar.

Los resultados fechados en los runbooks son históricos, no checks ejecutados en esta revisión.
Los modelos deterministas prueban contratos; no sustituyen evaluación de calidad con material
revisado y un proveedor real explícitamente autorizado.

## Límites y colaboración

Retrieval actual: FTS español AND sobre pregunta original, máximo cinco fragmentos. No hay
consulta derivada, embeddings, pgvector, planner estructurado ni tool loop. Sin evidencia se
abstiene; validar citas prueba procedencia/literalidad, no el soporte de cada afirmación.

Preservar curso/corpus autorizado, pregunta original, idempotencia, evidencia durable y validación
antes de publicar. No hay conocimiento general como fallback, OCR ni acceso docente implícito a
historias privadas. Las decisiones de piloto/modelo/privacidad siguen abiertas.

Leer [AGENTS.md](AGENTS.md) antes de modificar el proyecto. La arquitectura propuesta y las
referencias históricas no autorizan nuevas dependencias o cambios de contrato por sí solas.
