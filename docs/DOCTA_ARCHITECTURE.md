# Docta — Arquitectura del sistema

**Revisión:** 2026-09-22. **Alcance:** contratos vigentes y evolución identificada por estado.
Esta página no sustituye los [ADR aceptados](adr/README.md) ni autoriza implementar el roadmap.
El [estado actual](CURRENT_STATE.md) vincula capacidades a código; [Fase 1](PHASE_1_TUTOR_QUALITY.md)
define el siguiente incremento. La [referencia v1.1](DOCTA_ARCHITECTURE_V1.1_REFERENCE.md)
conserva el diseño extenso anterior, sin convertirlo en requisitos nuevos.

## 1. Producto y límites

Docta es un tutor pedagógico basado en evidencia autorizada del curso. Busca ofrecer explicaciones,
pistas, preguntas y verificación de intentos apropiadas para la actividad; no es un chatbot de
conocimiento general ni un sistema cuya calidad quede probada por tener citas.

Hoy se admite PDF digital con texto. No hay OCR, comprensión multimodal, GraphRAG, integración LMS
ni ejecución multiagente. La Fase 0 demostró el recorrido técnico; la calidad tutorial y la
preparación del piloto siguen pendientes de evaluación.

El único régimen factual admitido es grounding estricto del curso. Una respuesta de conocimiento
general, aunque sea correcta, no sustituye evidencia curricular. Un régimen diferente requiere
decisión de producto, contrato y ADR; no es un fallback implícito.

## 2. Organización y autoridad

Docta es un monolito modular con procesos web, API y worker de ingesta. No hay un microservicio
RAG ni una instancia de ejecución por estudiante.

| Responsabilidad | Implementación vigente | Evolución propuesta |
| --- | --- | --- |
| Plataforma y autorización | FastAPI, OIDC, membresías y casos de uso por curso. | Políticas adicionales sin transferir autoridad al modelo. |
| Conocimiento documental | Upload S3, PyMuPDF, corpus inmutable y PostgreSQL FTS. | Consultas derivadas y candidatos de recuperación detrás del puerto. |
| Tutoría | `TutorRequest`, `TutorDraft`, prompt e historial acotado. | Política versionada, actividad, intentos y ayudas explícitos. |
| Ejecución conversacional | `ConversationRuntime` en API; una recuperación y hasta una generación. | Runtime pedagógico acotado, sujeto a decisión específica. |
| Ejecución de ingesta | `JobDispatcher`, outbox PostgreSQL, Redis Streams y worker. | No reutilizar automáticamente su cola para conversaciones. |
| Presentación | Next.js BFF, PKCE, assistant-ui, CSS propio y componentes de UI. | Adaptaciones de presentación sin autoridad pedagógica. |
| Evaluación | Tests técnicos con modelos deterministas. | Harness offline y dataset revisado, Incremento 5. |

Los nombres plataforma/conocimiento/tutoría describen responsabilidades, no un mandato de mover
carpetas. La agrupación K/I/T/E del documento antiguo y el sistema KITE de la literatura son
conceptos distintos. La pedagogía ya figuraba como objetivo en v1.1; el nuevo documento propone
otra forma de conducir la ejecución, no descubre por primera vez ese objetivo.

## 3. Flujos implementados

### Ingesta y publicación

1. La API autoriza una carga por curso y devuelve un presigned PUT de duración limitada.
2. El cliente sube directamente al almacenamiento. La confirmación verifica el objeto y guarda
   versión, job y outbox de forma consistente; no ejecuta el parser dentro de la petición.
3. El worker publica/consume identificadores mediante Redis Streams. PostgreSQL decide estado,
   lease, intento y derecho de publicación; un worker vencido no puede confirmar resultados.
4. PyMuPDF extrae texto y página; el chunking por caracteres conserva procedencia. Errores de
   formato, cifrado o ausencia de texto son explícitos, sin OCR silencioso.
5. Se indexa un corpus inmutable. La activación requiere estado `READY` y compare-and-swap.

El flujo actual contiene una versión documental por corpus; no implica un editor multiversión
ni un catálogo publicable de múltiples PDFs por corpus. La publicación de un corpus nuevo no
altera las preguntas ya aceptadas. Véanse [ADR 0001](adr/0001-ingestion-durability-and-response-delivery.md)
y [operación de ingesta](runbooks/ingestion.md).

### Conversación

```text
Autorizar + idempotencia + capturar corpus + persistir pregunta pending
  → recuperar evidencia con la pregunta original
  → persistir evidencia
  → generar TutorDraft si hay evidencia / abstenerse sin modelo si no hay
  → validar esquema, alcance y citas
  → confirmar respuesta y citas atómicamente
  → entregar resultado persistido por SSE o lectura de historial
```

El modelo puede abstenerse aun con fragmentos recuperados. Una abstención válida es `completed`,
no `failed`. Un fallo del proveedor, del formato o de persistencia no se disfraza de abstención.
El runtime no reformula consultas, ejecuta herramientas ni repara candidatos automáticamente.

## 4. Puertos existentes y sus contratos

| Puerto o límite | Código | Garantía / límite |
| --- | --- | --- |
| Identidad | [identity.py](../apps/api/src/docta_api/identity.py) | Claims OIDC verificados; la membresía local autoriza cada operación. |
| Almacenamiento | [object_storage.py](../apps/api/src/docta_api/object_storage.py) | Contrato S3 para carga directa y objetos versionados. |
| Retrieval | [rag.py](../apps/api/src/docta_api/rag.py), [postgres_retriever.py](../apps/api/src/docta_api/postgres_retriever.py) | Devuelve `Evidence`, no una segunda respuesta generada. |
| Modelo | [tutor_http.py](../apps/api/src/docta_api/tutor_http.py) | Adaptador HTTP configurable; salida estructurada, límites y sin retry automático. |
| Validación | [rag.py](../apps/api/src/docta_api/rag.py) | Esquema, citas recuperadas y literalidad; no prueba soporte semántico completo. |
| Ejecución | [conversation_runtime.py](../apps/api/src/docta_api/conversation_runtime.py) | Secuencia API con deadline; independiente del socket. |
| Persistencia | [conversations.py](../apps/api/src/docta_api/conversations.py) | Pregunta antes de inferencia y confirmación condicional del resultado. |

El scope de retrieval conserva curso, corpus, usuario, conversación, mensaje y correlación.
La evidencia incluye documento/versiones, páginas, fragmento, contenido y hashes. No reducir
estos contratos para copiar tipos ilustrativos de una propuesta.

`PostgresRetriever` exige que `Message.question == question`, además de verificar ownership,
membresía, estado y deadline. Una consulta derivada requiere un contrato separado y auditable;
no se habilita quitando esa condición. Es una decisión pendiente que refina ADR 0002.

## 5. Persistencia y entrega

- `Message` representa hoy una pregunta y su respuesta nullable: `pending → completed|failed`.
- Una conversación admite un solo mensaje pendiente; la misma clave/texto devuelve el existente,
  y la misma clave con contenido diferente produce conflicto.
- El corpus se captura al aceptar; evidencia y citas quedan vinculadas al mismo mensaje y alcance.
- Las citas históricas mantienen procedencia auditable tras retirada del documento. No se borra
  físicamente un chunk referenciado para resolver un fallo de operación.
- La respuesta y sus citas se confirman juntas; las escrituras tardías no completan mensajes vencidos.
- SSE comunica aceptación, progreso y resultado validado/persistido. No publica tokens sin validar.
- Desconectar el navegador no cancela la generación. Caer el proceso no garantiza reanudación:
  al vencer el deadline, los pendientes abandonados se reconcilian como fallidos.
- Repetir el POST observa la misma operación; no reintenta una llamada ambigua al proveedor.
- Los IDs SSE sirven para correlación, no constituyen un event log reproducible.

No crear tablas paralelas de conversación porque un framework tenga memoria propia. Separar
`Turn`, `Run`, `Activity` y checkpoints es una propuesta futura que necesita migración y mapeo
al agregado existente. Véase [ADR 0002](adr/0002-durable-conversation-and-scoped-rag.md).

## 6. Web y límites de confianza

Next.js mantiene el access token en una cookie cifrada HttpOnly; no en localStorage ni props del
cliente. La sesión tiene duración acotada y no almacena refresh tokens. El BFF limita rutas,
valida Origin en mutaciones y conserva claves idempotentes; FastAPI sigue siendo la autoridad.

PDFs, mensajes, historial y texto del proveedor son datos no confiables. No se ejecuta código
generado ni se interpreta una instrucción en un PDF como política del sistema. La UI muestra
contenido confirmado y fragmentos citados; no es un visor PDF completo ni un panel de profesores
con acceso a historiales ajenos. [ADR 0003](adr/0003-browser-workspace-and-oidc-session.md).

## 7. Dos harnesses y una frontera pendiente

El **harness de evaluación** ejecuta casos y mide resultados fuera del flujo normal del usuario.
El **harness pedagógico** conduce el turno en producción. El primero no modifica por sí solo el
segundo. Tener un runtime propio significa poseer políticas, contratos y estado, no prohibir librerías.

La propuesta de [harness pedagógico](DOCTA_HARNESS_ARCHITECTURE.md) añade decisiones locales y
herramientas con presupuestos. No está implementada ni sustituye la prohibición de agent loops
de ADR 0004. El plan vigente contempla contratos pedagógicos y una segunda recuperación acotada,
no ejecución autónoma general. Elegir entre esas alternativas necesita decisión explícita.

Puede mejorarse un retriever dentro de un flujo lineal; un bucle también puede recuperar mal.
Calidad de búsqueda, política de ejecución y ayuda pedagógica son dimensiones separadas.

## 8. Evaluación y observabilidad

El siguiente incremento es un runner offline sobre el FTS real, con 30–50 casos revisados,
corpus reproducible y recursos aislados. Mide recuperación/ranking, answerability, grounding,
pedagogía y operación por separado. Los fakes prueban contratos, no calidad del modelo.
Los criterios completos viven en [Fase 1](PHASE_1_TUTOR_QUALITY.md).

Las trazas operativas contienen identificadores, operaciones, duración y resultados seguros.
No contienen PDF, prompt, respuesta, razonamiento privado, tokens de autorización ni payloads
del proveedor. Artefactos de evaluación con contenido requieren acceso, consentimiento/procedencia
y retención propios; nunca se exportan automáticamente historiales privados como dataset.

Un índice más relevante no demuestra aprendizaje. Las pruebas con estudiantes simulados tampoco
sustituyen evaluación de transferencia/retención con personas y un protocolo aprobado.

## 9. Decisiones abiertas y exclusiones

El [registro de decisiones](DECISIONS.md) es el índice de preguntas pendientes: consulta derivada,
retrieval experimental, estado/política pedagógica, runtime/herramientas, proveedor, privacidad
y piloto. No hay selección nueva de LangGraph, LlamaIndex, Pydantic AI o Agents SDK en esta revisión.
Pydantic ya valida contratos/configuración; no equivale a haber adoptado Pydantic AI.

pgvector, RRF, reranker, parent–child, verificador matemático y OpenUI son candidatos, no
dependencias obligatorias. Kubernetes, microservicios, OCR, GraphRAG y multiagente siguen fuera
del alcance activo. Backups restaurados, cuotas, costes, operación productiva y evaluación con
el proveedor real son pendientes distintos del rebuild técnico de tests.

## 10. Cómo evolucionar esta arquitectura

1. Identificar una capacidad y el fallo/requisito que justifica cambiarla.
2. Capturar baseline, datos revisados y criterio de aceptación antes de comparar candidatos.
3. Registrar un ADR cuando cambie un contrato aceptado; enumerar qué parte sustituye.
4. Implementar una ruta vertical preservando aislamiento, durabilidad y evidencias históricas.
5. Actualizar inventario, runbook y plan solo con evidencia de lo realmente construido.

La historia permanece en [Fase 0](WALKING_SKELETON.md), los ADR y las referencias originales.
Los comandos operativos están en el [README](../README.md) y los [runbooks](runbooks/README.md),
no en los ejemplos conceptuales archivados.
