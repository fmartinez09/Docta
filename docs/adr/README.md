# Decisiones de arquitectura

Los ADR registran decisiones en su contexto temporal. Sus números son identificadores de
decisión, no números de incremento. Un ADR posterior sustituye solo las partes que identifica
explícitamente; conservar el documento anterior permite reconstruir por qué se construyó así.

**Revisión del índice:** 2026-09-22. Los textos de 0001–0004 conservan sus decisiones y fechas;
las notas posteriores aclaran estado sin simular una aceptación nueva.

| ADR | Estado vigente | Alcance |
| --- | --- | --- |
| [0001 — Durable ingestion before conversational RAG](0001-ingestion-durability-and-response-delivery.md) | Aceptado; ingesta implementada en 2B y SSE en 3. | PostgreSQL/outbox + Redis Streams, worker idempotente y resultado SSE validado. La elección de gateway se concreta en 0002 sin desplegarlo. |
| [0002 — Durable conversation and scoped RAG](0002-durable-conversation-and-scoped-rag.md) | Implementado en 3; sigue vigente. | Ownership, pregunta durable, FTS estricto, evidencia/citas, ejecución API y proveedor configurable. 0003 añade perfiles de compatibilidad. |
| [0003 — Minimal browser workspace and OIDC session](0003-browser-workspace-and-oidc-session.md) | Implementado en 4; sigue vigente. | BFF, PKCE, cookie cifrada, assistant-ui, idempotencia compatible y E2E. |
| [0004 — Phase transition and evidence-gated tutor quality](0004-phase-transition-and-evaluation-gates.md) | Adoptado para planificación; implementación de Fase 1 pendiente. | Cierre de Fase 0, evaluación primero, adopción condicionada de retrieval híbrido y separación entre diseño e implementación. |

El [estado actual](../CURRENT_STATE.md) enlaza la evidencia de implementación. El
[plan de Fase 1](../PHASE_1_TUTOR_QUALITY.md) identifica las futuras decisiones sobre consulta
derivada, índices, contratos pedagógicos y preparación del piloto. No crear ADR vacíos para
capacidades que todavía no necesitan una decisión.

## Qué no se decidió todavía

El [registro de decisiones](../DECISIONS.md) identifica preguntas abiertas, incluyendo consulta
derivada, índices, actividad/política, modelo/piloto y runtime adaptativo. El
[documento de harness](../DOCTA_HARNESS_ARCHITECTURE.md) es propuesta reconciliada, no ADR 0005
implícito. No se ha adoptado LangGraph, LlamaIndex, Pydantic AI, Agents SDK ni conocimiento general.

Para aceptar el bucle de herramientas habría que identificar las partes de 0002/0004 que cambian,
definir acciones/presupuestos/recuperación y actualizar el plan y reglas. No borrar la prohibición
anterior como si nunca hubiera existido. Las garantías de aislamiento, evidencia, idempotencia y
validación permanecen aunque se sustituya un adaptador.

## Convención de mantenimiento

Un nuevo ADR debe contener fecha, estado explícito, contexto, alternativas, decisión, consecuencias,
contratos sustituidos, evidencia/gates y reversión. `Proposed` no es `Accepted`; aceptación tampoco
equivale a implementación. Cambios editoriales no deben alterar retrospectivamente la decisión.
