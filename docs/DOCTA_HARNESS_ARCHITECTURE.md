# Docta — Harness de evaluación y runtime pedagógico

**Revisión:** 2026-09-22. **Estado:** diseño reconciliado; no es un ADR de adopción del bucle.
**Próxima implementación autorizada:** Incremento 5, evaluación offline y dataset revisado.

Esta página contrasta la [propuesta consolidada original](DOCTA_HARNESS_ARCHITECTURE_REFERENCE.md)
con el código y los ADR. La propuesta se conserva íntegra como referencia, incluidos sus contratos
ilustrativos, bibliografía y límites experimentales. Las diferencias pendientes se registran en
[DECISIONS.md](DECISIONS.md). No se ha elegido un nuevo framework.

## 1. Dos significados de harness

| Componente | Responsabilidad | Situación |
| --- | --- | --- |
| Evaluation harness | Preparar casos/corpora, ejecutar el sistema, medir y comparar versiones. | Incremento 5 pendiente; no altera por sí solo producción. |
| Harness pedagógico / tutor runtime | Conducir el turno, componer contexto, aplicar política, ejecutar acciones y confirmar respuesta. | `ConversationRuntime` existe con flujo fijo. Su evolución adaptativa es propuesta. |

Una explicación anterior centrada solo en evaluación no describía todo el documento de harness.
Necesitamos medir la ejecución actual y evaluar cualquier runtime candidato por separado.

«Harness propio» significa que Docta posee sus contratos, autoridad y estado. Puede usar Pydantic
o un adaptador de framework sin ceder esas responsabilidades. Pydantic para validación, Pydantic AI
para orquestación y un runner de evaluación son elecciones diferentes.

## 2. Punto de partida real

El [runtime actual](../apps/api/src/docta_api/conversation_runtime.py) recupera una sola vez con
la pregunta original, guarda evidencia, llama al modelo si hay fragmentos, valida y confirma.
Con recuperación vacía produce abstención sin modelo. No tiene herramientas ni reparación automática.

Ya existen `Retriever`, `TutorModel`, `ResponseValidator`, `RetrievalScope` y `Evidence` en
[rag.py](../apps/api/src/docta_api/rag.py). No se parte de un `ask_rag()` que responde dos veces.
También existen idempotencia, corpus capturado, evidencia durable, commit atómico y SSE independiente
del socket. Toda nueva ruta debe conservarlos, no posponerlos a una entrega final de endurecimiento.

Faltan actividad/intentos/ayudas estructurados, política versionada, consultas derivadas, selección
de herramientas, contador durable de presupuestos y recuperación entre pasos. El historial acotado
para generación no equivale a estado pedagógico ni a resolución conversacional para retrieval.

## 3. Qué añade la propuesta

El documento original propone un flujo externo controlado por código con un bucle interno acotado:

```text
Admitir y persistir → cargar contexto/política/actividad
  → modelo propone intervención o herramienta
  → código autoriza y ejecuta → observación → modelo
  → validar candidato → commit condicional → entregar respuesta
```

El modelo propone; Docta controla alcance, herramientas permitidas, presupuestos, evidencia y
publicación. Esto cambia la ejecución del tutor, no solo el prompt. Una pregunta al estudiante
puede cerrar el turno correctamente mientras la actividad continúa esperando su siguiente intento.

La separación propuesta es útil, pero no obliga a ocho servicios, ocho modelos o nuevas tablas
por cada concepto:

| Responsabilidad propuesta | Punto de partida / cautela |
| --- | --- |
| Turn admission / commit | Evolucionar `ConversationService`; conservar agregado `Message` e idempotencia. |
| Session/context builder | Reutilizar historial autorizado; añadir proyecciones de actividad cuando se aprueben. |
| Policy resolver | Introducir política explícita por curso/actividad, sin tomar instrucciones de PDFs. |
| Tutor runtime | Adaptar la secuencia actual; contrato con eventos tipados si se acepta un motor alternativo. |
| Tool executor | Autorizar argumentos/alcance, aplicar límites y registrar resultados; no dar SQL al modelo. |
| Response gate | Preservar validador actual; evaluación semántica/pedagógica adicional no es garantía perfecta. |
| Activity / learner observations | Persistir hechos e hipótesis por separado; no inferir dominio desde «entendí». |

## 4. Recuperación como herramienta: contrato a evolucionar

La primera herramienta candidata es `search_course_material(query, purpose)`, una función local
que devuelve evidencia. El modelo no elige usuario, curso, corpus, permisos, SQL ni credenciales.
El backend inyecta el alcance verificado y mantiene el corpus capturado durante todas las búsquedas.

Antes de habilitarla:

- Separar pregunta original y consulta derivada versionada, ligadas al mismo mensaje autorizado.
- Refinar la comprobación actual `Message.question == question` mediante ADR; no quitarla sin
  sustituir la garantía. Probar manipulación, curso ajeno y cambio de publicación durante el turno.
- Conservar todos los IDs, hashes, páginas y texto necesarios para evidencia/citas históricas.
- Definir resultados tipados: evidencia disponible, vacío, fallo de infraestructura y denegación.
  Vacío no demuestra ausencia global en el corpus; indisponibilidad no es falta de evidencia.
- Persistir qué evidencia fue recuperada y cuál se usó en la respuesta; una búsqueda nueva no
  autoriza citar fragmentos no recibidos o de otro alcance.
- Revalidar acceso vigente; un snapshot inmutable de documentos no congela permisos.

`get_source_fragment` solo se justifica si hace falta ampliar evidencia conocida; no debe permitir
leer documentos arbitrarios por IDs aportados por el modelo. Embeddings, FTS, fusión, reranking y
parent–child permanecen detrás del puerto, como experimentos independientes de la orquestación.

## 5. Evidencia obligatoria y búsqueda selectiva

| Situación | Diseño a evaluar | Restricción |
| --- | --- | --- |
| Pregunta factual sobre el material | Recuperar evidencia o reutilizar evidencia válida según contrato futuro. | Nunca sustituirla por memoria general del modelo. |
| Continuación de un ejercicio | Cargar enunciado, intento, ayudas y evidencia pertinentes. | Mantener versiones y permisos; historial del tutor no es fuente curricular. |
| Agradecimiento / coordinación | Respuesta breve sin búsqueda nueva. | Requiere un modo no factual compatible; el contrato actual no lo implementa. |
| Evidencia insuficiente | Abstenerse; futura aclaración o segunda búsqueda bajo política aprobada. | No ocultar un fallo técnico ni ampliar permisos. |
| Explicación matemática sin evidencia curricular | Otro régimen de producto, si se decide permitirlo. | Fuera del plan actual; no se habilita con este documento. |

En el contrato vigente, todo `TutorDraft` no abstentivo requiere evidencia y citas. Por tanto,
«responder sin nueva búsqueda» necesita diseñar reutilización o un modo no factual; no basta con
omitir `retrieve()`. La excepción de conocimiento general del original queda no adoptada.

## 6. Estado pedagógico y política

Una primera actividad debería limitarse a un tema y ejercicios revisados. Registrar enunciado y
versión, objetivo, modo de ayuda, intento observado, ayudas ya dadas y pregunta pendiente. La
dificultad inferida es una hipótesis con procedencia, no un diagnóstico personal ni una prueba de dominio.

La política puede distinguir consulta conceptual, práctica guiada y ejemplo resuelto permitido.
No tiene por qué imponer una pregunta socrática en cada respuesta. El docente debe revisar el nivel
de ayuda y los casos de solución prematura; cambiar esta política exige evaluación y contrato.

Persistir actividad no exige guardar razonamiento privado. Los resúmenes futuros serían vistas
derivadas, recuperables desde hechos originales y no sustitutos irreversibles del historial.
Verificación matemática, banco de práctica y clasificación separada de solucionarios son extensiones,
no requisitos iniciales ni barreras de acceso ya implementadas.

## 7. Durabilidad, presupuestos y proveedor

El diseño futuro debe especificar si existen intentos separados de una pregunta lógica, cómo se
contabilizan herramientas/inferencias, quién conserva el derecho de publicar y qué ocurre al expirar.
Los contadores no pueden reiniciarse por reconectar SSE o reintentar una operación.

Los límites originales —hasta cuatro inferencias, tres herramientas, dos búsquedas, una reparación
y 45 segundos— son hipótesis experimentales, no configuración vigente ni SLO aceptado. Varias
inferencias pueden empeorar latencia y coste; medir el turno completo, no solo búsquedas ahorradas.

El adaptador actual consume una respuesta estructurada, no tool calls. Que el servidor sea compatible
con un endpoint de chat no demuestra que soporte correctamente herramientas, argumentos, resultados
y presupuestos. Esa compatibilidad debe probarse antes de adoptar un loop.

Una segunda recuperación no es un retry de proveedor. El runtime vigente no reejecuta inferencias
ambiguas tras un crash. Cambiar eso requiere política explícita sobre coste, duplicación y estados.
Un checkpoint tampoco programa recuperación por sí solo ni es atómico con las tablas de Docta.

Mantener validación completa, commit condicional y entrega de resultado confirmado. La desconexión
del navegador no implica cancelación. Cancelación explícita, reanudación entre pasos y nuevos eventos
SSE son capacidades pendientes; no reemplazar silenciosamente el contrato `message.*` existente.

## 8. Alternativas de runtime y librerías

El original propone comenzar con un bucle pequeño y considera LangGraph o Pydantic AI según las
necesidades. Las conversaciones también mencionaron LlamaIndex y Agents SDK. Ninguno está seleccionado
por un ADR nuevo ni es requisito del Incremento 5.

La comparación futura debe preguntar qué trabajo concreto resuelve cada adaptador: tools, estado,
reanudación, observabilidad, compatibilidad del proveedor y coste de migración. No basta contar
features ni llamar «propio» a un framework envuelto. No combinar motores sin definir un único dueño
de retries, estado y finalización. Una interfaz no hace gratuita una migración de ejecuciones pendientes.

El punto de adopción más pequeño para una librería de retrieval sería un adaptador detrás del
puerto existente. Eso no obliga a cambiar el runtime tutorial ni mejora automáticamente los resultados.

## 9. Evaluación antes y durante la evolución

La especificación activa del runner/dataset está en [Incremento 5](PHASE_1_TUTOR_QUALITY.md#incremento-5--evaluation-harness--dataset-v0).
Se mantienen 30–50 casos revisados; las 40–60 conversaciones del original son una sugerencia para
evolucionar cobertura, no un segundo criterio de cierre contradictorio.

| Línea | Qué comparar | Qué no concluir |
| --- | --- | --- |
| Ingeniería | Aislamiento, fallos, idempotencia, budgets y publicación. | Un fake correcto no prueba calidad del modelo. |
| Retrieval | Evidencia esperada y ranking, sobre mismo corpus/consultas. | Más chunks o un score mayor no prueban soporte factual. |
| Tutoría | Corrección, soporte, ayuda pertinente y continuidad multiturno revisadas. | Fluidez o preferencia automática no equivalen a aprendizaje. |
| Acciones, si se acepta el loop | Búsquedas necesarias/omitidas/innecesarias, herramientas inválidas y agotamiento. | Una respuesta correcta no valida cualquier camino de ejecución. |
| Aprendizaje | Siguiente intento, transferencia y retención según protocolo. | Estudiante simulado no demuestra aprendizaje humano. |

Comparar un factor a la vez: baseline actual; política/estado con retrieval fijo; retrieval candidato
con generador fijo; runtime adaptativo frente a ruta controlada. No cambiar simultáneamente modelo,
prompt, embeddings, chunking, ranking y autonomía. Registrar también casos fallidos y no medidos.

La bibliografía del original sustenta hipótesis, no resultados de Docta. KITE distingue métricas RAG,
revisión experta y estudiantes simulados; Self-RAG incluye entrenamiento específico, no reproducido
por añadir una herramienta. No se afirma aquí una auditoría completa de las referencias externas.

## 10. Migración reconciliada y decisiones pendientes

| Paso | Alcance | Condición |
| --- | --- | --- |
| A — Inventario y baseline | Reutilizar puertos actuales, runner aislado y casos revisados. | Incremento 5 vigente; no alterar producción para mejorar el baseline. |
| B — Mejorar la recuperación medida | Consulta derivada o retriever candidato según fallo. | ADR/gates de 6–7. No exige esperar a un agent loop. |
| C — Primera actividad/política | Un ejercicio y varios turnos con intentos y ayudas observables. | Precisar alcance de 8 y contrato/persistencia; orden distinto requiere revisión del plan. |
| D — Decidir/adaptar ejecución | Comparar ruta tipada y eventual loop de herramientas. | Resolver D-05; modificar expresamente 0002/0004 y reglas antes de implementarlo. |
| Transversal — Confirmación y fallos | Preservar garantías actuales en cada paso, ampliar cuando aparezcan nuevos estados. | No posponer seguridad/durabilidad hasta terminar el loop. |
| E — Extensiones | Runtime durable, verificador, banco de práctica, presentación adicional. | Solo por requisito o mejora medida y alcance aprobado. |

Esto no sustituye la secuencia 5–15 del plan activo. Hace explícito cómo encajaría la propuesta
y dónde falta una decisión. La instrucción final del original de «extraer RetrievalPort y añadir
TutorActivity» no es el siguiente slice autorizado: el puerto ya existe y el baseline sigue primero.

## 11. Condición para aceptar el bucle

Un ADR futuro debe fijar acciones permitidas, evidencia obligatoria, consulta derivada, política
de intentos/retries, límites, estados/eventos, compatibilidad del proveedor y evaluación comparativa.
Debe identificar qué sustituye de ADR 0004 y conservar las garantías de ADRs 0001–0003.
Hasta entonces, esta es una propuesta trazable y el runtime fijo continúa siendo el contrato vigente.
