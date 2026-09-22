# Docta — Historia y registro de decisiones

**Revisión:** 2026-09-22. Esta página relaciona antecedentes, decisiones y pendientes.
No acepta por sí misma una arquitectura ni sustituye los [ADR](adr/README.md).

## 1. Estados documentales

| Estado | Significado |
| --- | --- |
| Implementado | Existe código identificable; los tests enlazados indican cobertura, no una ejecución reciente implícita. |
| Aceptado | Un ADR o decisión explícita fija el contrato; puede quedar implementación pendiente. |
| Planificado | Resultado previsto en Fase 1 con dependencias/gates; no es autorización para construirlo ahora. |
| Propuesto | Alternativa razonada aún no aceptada. |
| Pendiente de decisión | Falta una elección de producto/arquitectura o evidencia para tomarla. |
| Histórico / sustituido parcialmente | Conserva contexto; la decisión posterior identifica la parte que reemplaza. |

Una modificación documental no pasa automáticamente una capacidad de propuesto a aceptado, ni
de aceptado a implementado. Una prueba antigua mantiene su fecha y alcance.

## 2. Cómo llegamos aquí

| Hito | Decisión / situación | Qué permanece y qué cambió |
| --- | --- | --- |
| Arquitectura inicial, 2026-09-03 | Tutor RAG en monolito modular; objetivos de búsqueda híbrida, pedagogía y UI. | [Referencia v1.1](DOCTA_ARCHITECTURE_V1.1_REFERENCE.md) conserva también la revisión de septiembre 9; no es una copia pura de v1.0. |
| Incrementos 0–2 | Camino mínimo: identidad, cursos, PDF, FTS y publicación. Ingesta inicialmente inline. | Base técnica histórica; ejecución inline sustituida en 2B. |
| ADR 0001, 2026-09-05 | Ingesta durable antes del chat: outbox, Redis Streams, leases/fencing; SSE para conversación. | Implementado en 2B/3. LiteLLM era candidato, no despliegue aprobado por defecto. |
| ADR 0002, 2026-09-07 | Pregunta durable, FTS original top-5, evidencia/citas, ejecución API independiente del socket. | Contrato actual. Adaptador HTTP configurable, sin gateway obligatorio ni retry de modelo. |
| ADR 0003, 2026-09-07 | BFF/PKCE, cookie cifrada, publicación y chat UI; perfiles de compatibilidad del modelo. | Implementado en 4. Smoke de proveedor no significa modelo elegido para piloto. |
| Cierre Fase 0, 2026-09-08 | Merge `e31d1aa` y registros técnicos fechados. | Demuestra el flujo; no calidad educativa ni preparación productiva. |
| ADR 0004, 2026-09-09 | Evaluación primero, 30–50 casos, híbrido experimental y grounding estricto. | Plan vigente; Incremento 5 pendiente. Excluye agent loops. |
| Propuesta consolidada de harness, incorporada a esta revisión | Tutoría con política/actividad y bucle de herramientas; retrieval como evidencia. | [Original](DOCTA_HARNESS_ARCHITECTURE_REFERENCE.md) y [reconciliación](DOCTA_HARNESS_ARCHITECTURE.md). No hay ADR que la adopte todavía. |
| UI, 2026-09-22 | Commit `8ee13a5`: componentes, estilos y E2E de workspace. | No implementa harness de evaluación, consulta derivada ni bucle de herramientas. |
| Reorganización documental, 2026-09-22 | Separación de estado, propuesta, historia, operación y pendientes. | Sin cambios de runtime, configuración, datos ni aceptación de un framework. |

## 3. Aclaraciones de las conversaciones de diseño

- Se utilizó «harness» para dos cosas: evaluación offline y runtime pedagógico. No son sinónimos.
- «Propio» describe la propiedad de políticas, estado y contratos; permite adaptadores/librerías.
- Se discutieron Pydantic AI, Agents SDK, LangGraph y LlamaIndex. La discusión no constituye una
  selección aceptada; el repo usa Pydantic para datos y HTTPX para el proveedor.
- El retrieval no es una llamada `ask_rag` que genera otra respuesta: `Retriever` ya devuelve
  evidencia. Una herramienta futura reutilizaría y evolucionaría ese puerto.
- FTS español no exige coincidencia literal de la pregunta completa, pero sus términos AND
  pueden perder evidencia. No es BM25 ni búsqueda semántica densa.
- Un timeout técnico, una recuperación vacía y una abstención del modelo son resultados diferentes.
  El [runbook de conversaciones](runbooks/conversations.md) explica cómo distinguirlos.
- Los ejemplos de puntuaciones y límites de las propuestas no son benchmarks medidos de Docta.
- Un pipeline lineal no implica por sí mismo mala recuperación; un agent loop no garantiza calidad.

Estas son correcciones conceptuales, no un diagnóstico nuevo sobre datos privados. Las trazas
aportadas en conversaciones no se copian automáticamente al repositorio ni se convierten en gold.

## 4. Decisiones pendientes

Los identificadores D-* son referencias de discusión, no ADR aceptados. No imponen nuevas fechas
ni responsables; el propietario debe acordar revisión docente y autoridad de aprobación.

| ID / estado | Pregunta por resolver | Evidencia / decisión necesaria | Relación con el plan |
| --- | --- | --- | --- |
| D-01 — pendiente | ¿Qué material autorizado, revisor y rúbrica forman dataset v0? | Procedencia, casos revisados y separación dev/held-out. | Antes de cerrar 5; schema/runner pueden avanzar con borradores explícitos. |
| D-02 — planificado | ¿Cómo ligar consulta derivada al mensaje original? | ADR que refine 0002, versión de consulta, alcance/corpus y tests de manipulación. | 6. No eliminar `Message.question == question` sin reemplazar su garantía. |
| D-03 — experimental | ¿FTS, dense o fusión mejoran recuperación? | Baseline, gates de calidad/latencia/coste; ADR/migración si se introduce pgvector. | 7; conservar FTS si no mejora. |
| D-04 — planificado / propuesta ampliada | ¿Qué política y estado pedagógico mínimos persistir? | Tema acotado, intentos/ayudas observables, rúbrica; distinguir hecho e hipótesis. | 8–9; `TutorActivity` amplía el diseño y requiere alcance explícito. |
| D-05 — propuesto, no aceptado | ¿Ruta tipada con segunda búsqueda o bucle de herramientas? | ADR que identifique cambios a 0002/0004, presupuestos, acciones, fallos y comparación. | El plan actual conserva 10 sin agent loop. No bloquea medir/mejorar retrieval. |
| D-06 — pendiente | ¿Qué runtime/librería usar si se acepta D-05? | Probar contrato de tools del proveedor, límites, persistencia, complejidad y reversión. | No exige elegir framework para 5. Un bucle explícito es propuesta, no adopción. |
| D-07 — planificado | ¿Cómo representar aclaración, abstención y evidencia reutilizada? | Contrato versionado y validación sin respuesta factual no respaldada. | 10; hoy solo cuatro modos y citas obligatorias fuera de abstención. |
| D-08 — fuera del plan actual | ¿Permitir conocimiento general sin respaldo del curso? | Decisión de producto, separación visible de régimen y ADR. | No habilitar como fallback ni por el solo hecho de omitir búsqueda. |
| D-09 — pendiente | ¿Proveedor, modelo y gateway para piloto? | Benchmark, compatibilidad real, privacidad, costes y SLO de latencia. | 11/15; smoke local no elige modelo. |
| D-10 — propuesta posterior | ¿Reanudación entre pasos, cancelación y múltiples intentos? | Estados/fencing, efectos idempotentes, política de coste y recuperación ensayada. | Extensión de 0002; no confundir checkpoint con ejecutor durable. |
| D-11 — pendiente | ¿Privacidad, retención, feedback y evaluación educativa? | Material permitido desde 5; protocolo/consentimiento para estudiantes, revisión humana. | 12–13/15; no exportar historiales por defecto. |
| D-12 — opcional / diferido | ¿Verificador, parent–child, reranker, OpenUI? | Necesidad concreta, comparación y gate del componente. | No requisitos previos del baseline ni del piloto por nombre de tecnología. |
| D-13 — pendiente | ¿Operación de piloto? | Backups restaurados, cuotas, límites de gasto, responsables, TLS/OIDC y criterios de parada. | 15; rebuild de tests no equivale a restore. |

## 5. Cómo tomar y registrar una decisión

1. Precisar la pregunta, alternativas, evidencia y alcance; no elegir varios componentes a la vez
   si eso impide atribuir una mejora.
2. Registrar aceptación explícita y un ADR cuando cambie un contrato; citar los puntos sustituidos.
3. Actualizar el plan y `AGENTS.md` si cambia alcance o restricciones. Mantener los ADR originales.
4. Implementar y verificar antes de marcar como construido en `CURRENT_STATE.md`.
5. Registrar resultados negativos, límites y condiciones de reversión; una propuesta rechazada
   sigue siendo parte de la historia, no se borra para aparentar consenso previo.
