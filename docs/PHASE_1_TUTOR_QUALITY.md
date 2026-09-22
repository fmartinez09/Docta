# Fase 1 — Calidad tutorial, retrieval y evaluación

**Fecha de adopción del plan:** 2026-09-09. **Revisión documental:** 2026-09-22.\
**Estado:** plan activo; implementación pendiente.\
**Próximo slice:** Incremento 5 — Evaluation Harness + Dataset v0.\
**Decisión de transición:** [ADR 0004](adr/0004-phase-transition-and-evaluation-gates.md).

## 1. Objetivo y punto de partida

La [Fase 0](WALKING_SKELETON.md) demostró que una pregunta puede recorrer el sistema desde
un PDF autorizado hasta una respuesta durable con citas auditables. Esta fase debe determinar
si Docta encuentra la evidencia adecuada y ofrece una intervención pedagógica útil, con coste
y latencia medidos. El [estado actual](CURRENT_STATE.md) describe el baseline implementado.

El éxito no se mide solo por respuestas correctas. Separamos cinco preguntas: ¿recuperó la
evidencia?, ¿debía responder?, ¿la respuesta está respaldada?, ¿la ayuda era apropiada?, ¿mejoró
el siguiente intento del estudiante? La última requiere un diseño de evaluación propio; las
medidas de satisfacción, un juez automático o un estudiante simulado no prueban aprendizaje real.

Este plan adapta las dos conversaciones de planificación aportadas por el propietario. Conserva
su secuencia de incrementos 5–15, con gates explícitos. Las referencias a KITE son inspiración
de diseño; no se atribuyen aquí resultados a un paper sin una revisión bibliográfica específica.
La agrupación interna K/I/T/E del monolito y el enfoque pedagógico inspirado en KITE son conceptos
distintos, aunque compartan el nombre en la arquitectura.

### Relación con la nueva propuesta de harness

El [harness pedagógico](DOCTA_HARNESS_ARCHITECTURE.md) propone estado de actividad y un bucle
acotado de herramientas. No es el **harness de evaluación** del Incremento 5. Esta revisión
documenta la diferencia sin aceptar el bucle ni cambiar la secuencia vigente de incrementos.
ADR 0004 permanece vigente; una adopción requiere resolver D-05 en el [registro de decisiones](DECISIONS.md).

La propuesta original sugiere extraer retrieval y añadir actividad como siguiente entrega.
En este repositorio `Retriever` ya devuelve evidencia y el siguiente slice sigue siendo medirlo.
La primera actividad puede concretar la evolución de 8; no se afirma implementada ni se adelanta
por la sola existencia del documento. Mejorar retrieval medido no depende de construir un loop.

## 2. Reglas de la fase

- Mantener aislamiento, corpus capturado, pregunta original, idempotencia, evidencia durable,
  citas reales y validación antes de exposición de ADRs 0001–0003.
- Medir el baseline antes de cambiarlo. Comparar un componente a la vez, sobre las mismas
  versiones de material y casos; conservar también resultados negativos.
- Las nuevas consultas o hipótesis pedagógicas nunca son evidencia curricular ni amplían permisos.
  Una cita siempre procede de un chunk real usado para formar la respuesta.
- Mantener el modo estricto de curso. Aclarar o abstenerse no autoriza responder con memoria general.
  El modo «fuera del material» queda fuera del plan hasta una decisión de producto y ADR propios.
- Versionar contratos y decisiones observables; no solicitar ni persistir chain-of-thought interno.
- No añadir servicios, agentes, dashboards ni plataformas de evaluación para ejecutar un benchmark local.
- Definir métricas, cortes por categoría y tolerancias de calidad/latencia/coste antes de comparar
  candidatos. No ajustar un gate después de ver resultados para declarar ganador al candidato preferido.

## 3. Secuencia y dependencias

| Incremento | Resultado buscado | Dependencia y estado |
| --- | --- | --- |
| 5 — Evaluation Harness + Dataset v0 | Baseline reproducible y errores clasificados. | Siguiente; pendiente. |
| 6 — Conversational Turn Resolution | Consultas derivadas para seguimientos sin perder el original ni el scope. | 5; pendiente. |
| 7 — Hybrid Retrieval | Comparación FTS/dense/híbrido y decisión de adopción. | 5–6; experimental, pendiente. |
| 8 — Minimal KITE Pedagogical Planner | Evidencia del aprendiz, decisión pedagógica e intención de retrieval explícitas. | 5–6 y baseline de retrieval fijado tras 7; pendiente. |
| 9 — Retrieval condicionado pedagógicamente | Experimento A/B/C sobre el mismo retriever. | 8 y dataset ampliado; experimental, pendiente. |
| 10 — Answerability + TutorResponse v2 | Responder, aclarar, recuperar una vez más o abstenerse con reglas verificables. | 8–9 y contrato versionado; pendiente. |
| 11 — Benchmark de modelos | Selección por calidad, latencia y coste, separando planner y generador. | Baselines y contratos estabilizados; pendiente. |
| 12 — Evaluación pedagógica y siguiente intento | Rúbrica experta y protocolo exploratorio de efecto de la intervención. | 10–11; pendiente. |
| 13 — Feedback y evolución del dataset | Promoción revisada de casos y splits protegidos. | Política de privacidad y 5/12; pendiente. |
| 14 — OpenUI | Presentación dinámica segura si la UI actual resulta insuficiente. | Contrato estable, necesidad medida; opcional, pendiente. |
| 15 — Preparación del piloto | Operación, privacidad, modelo evaluado y criterios de salida de piloto. | Gates de calidad/operación; pendiente, no depende de adoptar OpenUI. |

Completar un experimento puede significar rechazar su candidato y conservar el baseline. La
numeración preserva continuidad; no obliga a introducir una tecnología que no mejore los resultados.
Privacidad, material autorizado y responsables de revisión se resuelven desde el dataset inicial;
no se posponen hasta el último incremento. Un hallazgo puede justificar reordenar el plan mediante
una decisión documentada, sin perder los gates anteriores.

## 4. Próximo trabajo concreto

### Incremento 5 — Evaluation Harness + Dataset v0

**Comportamiento vertical:** a partir de PDFs de evaluación revisados y casos versionados, ejecutar
la ingesta y el retriever actual sobre recursos aislados, guardar resultados por caso y producir un
reporte reproducible de cobertura, ranking, abstención y limitaciones de evaluación. Sin cambiar
la política de producción, el prompt, el retriever ni el modelo para mejorar artificialmente el baseline.

**Módulos previstos:** una carpeta `evals/` con schema/dataset/runner y documentación de uso;
tests de métricas y validación, integración con los puertos reales de ingesta y retrieval. Reutilizar
`docta_api` y la infraestructura de tests; no crear un servicio ni tablas de producto preventivamente.
Definir las rutas y el comando exactos durante la implementación y documentarlos cuando existan.

#### Dataset y procedencia

Crear 30–50 casos en español con cobertura de conceptos, ejercicios, intentos erróneos,
seguimientos, pronombres/elipsis, cambio de tema, ambigüedad, falta de evidencia y aislamiento.
Incluir pruebas con corpus inactivo y material ajeno como señuelo. Los casos de parsing inválido
y durabilidad ya cubiertos por tests siguen siendo regresiones, no métricas de calidad del tutor.

Cada caso debe contener:

- `case_id`, versión de schema/dataset, categoría, dificultad y split;
- referencia al curso de prueba y manifiesto de corpus/documentos con checksum y versión de pipeline;
- pregunta exacta e historial mínimo sintético o autorizado; intento del estudiante cuando corresponda;
- answerability esperada y evidencia relevante por documento, página, fragmento/hash, con relevancia
  binaria o graduada documentada; evidencia prohibida y variantes aceptables cuando corresponda;
- interpretación/consulta autónoma esperada cuando aplique; intención y rol de evidencia como etiquetas,
  sin implementar todavía un planner;
- intervención pedagógica aceptable y prohibida, incluida la entrega prematura de una solución;
- autor, revisor, estado de revisión, versión de rúbrica y procedencia/permiso de uso del material.

Los UUID generados al reconstruir una base no son etiquetas portables. El runner debe resolver el
manifiesto a los IDs reales del corpus/chunks de esa ejecución y fallar si una referencia es inválida
o ambigua. No relabelar automáticamente el gold usando los resultados del retriever.
Separar `draft` de `reviewed`; casos generados por modelo requieren revisión humana antes de contar
como gold. Si falta revisión, entregar el harness y los borradores, manteniendo abierto ese criterio.

Reservar casos held-out desde v0, separar familias/variantes cercanas para evitar contaminación
y declarar que una muestra pequeña produce resultados exploratorios. No usar el held-out para
escoger prompts, umbrales o candidatos. No versionar conversaciones privadas, secretos ni PDFs
sin permiso; los artefactos con contenido educativo necesitan acceso y retención definidos.

#### Runner y reporte

El camino reproducible usa PostgreSQL/MinIO/Redis aislados y el parser/retriever reales, con
identidades de prueba y mensajes autorizados creados por los casos de uso. No llamar a una copia
de SQL sin sus verificaciones de mensaje/membresía. Limpiar solo recursos generados para el run;
nunca derivar destinos de borrado desde `.env` de desarrollo.

Separar dos modalidades:

1. **Sin proveedor externo:** métricas reales de retrieval; pruebas deterministas de contratos,
   clasificación de vacíos y reportes. Un fake del tutor valida integración y cálculo de métricas,
   pero sus puntuaciones no se publican como calidad pedagógica o de generación del baseline.
2. **Evaluación explícita con modelo:** mismo dataset/pipeline y proveedor configurado, con límites
   de solicitudes, tiempo y gasto. Produce artefactos para revisión humana de grounding y pedagogía.
   No se ejecuta implícitamente en CI ni requiere APIs pagadas para pasar tests normales.

| Capa | Medición inicial | Regla de interpretación |
| --- | --- | --- |
| Retrieval | Recall@K, hit rate, MRR@K y nDCG@K, al menos K=5. | Solo casos con evidencia relevante; nDCG usa la escala de relevancia declarada. Denominadores y casos excluidos visibles. |
| Answerability | Falsa abstención y falsa respuesta. | Falsa abstención: abstenciones entre casos respondibles. Falsa respuesta: respuestas factuales entre no respondibles. Fallos técnicos se reportan aparte, no como abstenciones. |
| Grounding | Integridad/literalidad de citas y soporte/cobertura de afirmaciones. | Integridad puede comprobarse automáticamente; soporte semántico requiere revisión o juez calibrado. |
| Pedagogía | Adecuación de ayuda, intención y solución prematura según rúbrica. | Revisión humana inicial; no inferir acierto desde coincidencia literal con una respuesta modelo. |
| Operación | Duración por etapa y total, fallos, tokens/coste cuando se disponga. | Configuración y tamaño de muestra visibles. Datos no disponibles como `not_measured`, no como cero. |

Si el modo sin modelo solo determina que no hay chunks, reportar esa señal como resultado de
retrieval, no como la answerability completa del tutor. Los tiempos de fakes no representan
latencia del modelo; los costes estimados deben declarar la tarifa/configuración usada.

Cada run guarda commit, fecha, dataset/corpus/pipeline, prompt y configuración de retrieval,
modelo/perfil cuando aplique, resultados por caso, agregados por categoría, fallos y métricas
no medidas. Usar un reporte estructurado y un resumen legible; contenido educativo fuera de logs
operacionales. Los casos fallidos no desaparecen de los denominadores sin explicación.

Clasificaciones diagnósticas iniciales: `QUERY_RESOLUTION`, `RETRIEVAL_MISS`, `FALSE_ABSTENTION`,
`FALSE_ANSWER`, `CITATION_INVALID`, `GROUNDING_UNSUPPORTED`, `PEDAGOGY_MISMATCH`, `TECHNICAL_FAILURE`.
Registrar `UNDETERMINED` cuando la evidencia no permita atribuir una causa; puede haber más de una.

#### Cobertura futura, sin ampliar el cierre de 5

Las 40–60 conversaciones propuestas en el documento de harness no reemplazan los 30–50 casos
revisados de este incremento. Versionar cualquier ampliación y distinguir caso de conversación:
un caso puede incluir historial mínimo, pero no equivale a un estudio longitudinal.

Si se acepta ejecución adaptativa, añadir posteriormente etiquetas de búsqueda necesaria,
evidencia ya disponible y respuesta no factual; medir omisiones, búsquedas innecesarias, argumentos
inválidos y agotamiento. Hasta implementar esa capacidad, registrar sus métricas como no medidas,
no atribuir al baseline herramientas inexistentes ni crear gold desde decisiones del propio modelo.

#### Criterios de aceptación

- [ ] Schema y validación rechazan casos incompletos, IDs repetidos y referencias de evidencia inválidas.
- [ ] 30–50 casos revisados con manifiesto reproducible, rúbrica, procedencia y split documentados.
- [ ] Runner invocable desde checkout limpio sobre recursos aislados; rebuild no invalida las etiquetas.
- [ ] Baseline FTS real reproducible con resultados por caso y métricas verificadas en rankings conocidos.
- [ ] Pruebas demuestran que curso B/corpus inactivo no aparecen en retrieval ni citas de A.
- [ ] Reporte separa contrato con fakes, calidad con modelo/revisión humana y métricas todavía no medidas.
- [ ] Baseline y diagnóstico de los casos conversacionales registrados; límites visibles sin declarar
      un smoke test como benchmark ni inventar puntuaciones humanas.
- [ ] Runbook del harness documenta configuración, recursos, versiones, limpieza y ejecución explícita con modelo.
- [ ] Tests acotados y checks completos del repositorio pasan; se actualiza el estado con comandos y resultados.

No se exige que el baseline tenga buena calidad para cerrar 5: se exige medirlo correctamente.
Una evaluación con proveedor externo no es requisito de CI; si queda pendiente, se registra como
gate abierto para comparar calidad de generación, escoger modelo y autorizar un piloto.

**Fuera de esta entrega:** `TutorActivity`, tool calling, cambios de prompt para subir puntuaciones,
selección de framework, pgvector y exportación de historiales privados. No son prerrequisitos del runner.

## 5. Evolución después del baseline

### Incremento 6 — Resolución conversacional

Derivar `standalone_question` y relación con el historial dentro de un contrato pequeño y versionado.
La pregunta original permanece intacta. Usar únicamente historial autorizado y acotado, sin convertir
afirmaciones previas del tutor en hechos del curso. Distinguir seguimiento, cambio de tema y ambigüedad;
si falta contexto, conservar incertidumbre en vez de inventar el objetivo del alumno.

**Decisión necesaria:** refinar ADR 0002. `PostgresRetriever` exige hoy `Message.question == question`;
no basta con pasarle texto reescrito ni eliminar esa condición. Separar la identidad/autorización del
mensaje de la consulta derivada y vincular ambas de forma auditable al mismo curso/corpus capturado.

**Gate:** mejora en casos conversacionales revisados, sin regresión relevante en preguntas directas,
sin ampliación de scope y con original/idempotencia preservados. Probar corpus cambiado durante el
turno y manipulación de consulta/historial. No añadir todavía modos públicos que el contrato no soporte.

El par «¿Y una derivada?» con y sin evidencia en el corpus sirve para distinguir abstención correcta
de falsa abstención. No asumir que «¿Qué es una derivada?» cambia favorablemente los términos FTS;
la causa y la mejora se comprueban con el runner.

### Incremento 7 — Retrieval híbrido experimental

Comparar FTS, dense y FTS+dense+RRF manteniendo fijos dataset, consulta resuelta, chunking y generador.
Introducir pgvector explícitamente mediante ADR, migración e imagen compatible en desarrollo/tests;
el Compose actual no lo incluye. Versionar modelo/dimensión/configuración de embeddings y construir
nuevos índices/corpora inmutables. Probar aislamiento, captura de corpus, activación tras indexación,
rebuild y conservación de citas históricas. No mutar embeddings de un corpus publicado.

**Gate:** mejora predefinida de recuperación/ranking con latencia/coste aceptables, sin regresiones
de aislamiento o integridad. Mantener búsqueda exacta inicialmente y dejar HNSW/reranker fuera.
Si RRF no gana, documentar el resultado y conservar el mejor baseline autorizado.

### Incremento 8 — Planner pedagógico mínimo

Formalizar `LearnerEvidence → PedagogicalDecision → RetrievalIntent`, con tipos y versiones.
Separar lo observado del intento y la hipótesis de error. Distinguir intención del alumno
(`concept_explanation`, `exercise_help`, `answer_check`, etc.), relación conversacional (`follow_up`)
y acción tutorial (pista, pregunta, explicación); no mezclarlas en una taxonomía gigante.

**Gate:** rúbrica revisada y tests de intervención, nivel de ayuda y evidencia solicitada; evaluación
contra la política anterior. No introducir diagnósticos personales ni un perfil psicológico persistente.
Un ADR fija contrato, versionado/persistencia y política de ayuda antes de exponer cambios al alumno.

### Incremento 9 — Retrieval condicionado por propósito pedagógico

Ampliar a 80–120 casos revisados. Comparar A: pregunta; B: pregunta + últimos turnos;
C: pregunta + intento/error hipotético + `evidence_role`, sobre el mismo retriever y corpus.
Fijar si la pregunta es original o resuelta y mantenerlo constante en los tres brazos.

**Gate:** mejor evidencia e intervención según evaluación ciega, con costes y errores de hipótesis
visibles; mayor fluidez no basta. Ningún brazo obtiene acceso adicional. Mantener un único factor
experimental variable y conservar el baseline si C no aporta una mejora suficiente.

### Incremento 10 — Answerability y TutorResponse v2

Diseñar las decisiones `ANSWER`, `CLARIFY`, `RETRIEVE_AGAIN`, `ABSTAIN`, separadas del estado
durable `pending/completed/failed`. Aclaración y abstención pueden ser resultados completados;
un fallo técnico no debe ocultarse como falta de evidencia.

**Gate:** ADR de contrato compatible/versionado, transiciones y presupuestos; máximo una segunda
recuperación bajo el mismo scope/corpus, sin loops ni repetición automática de una llamada ambigua
al proveedor. Persistir y validar qué evidencia se usó; probar deadline, errores, idempotencia y SSE.
Medir falsa abstención/falsa respuesta y evitar respuestas factuales no respaldadas al aclarar.

### Incremento 11 — Benchmark de modelos

Comparar el baseline local con candidatos configurados, separando planner y generador. Mantener
constantes evidencia y contratos para atribuir diferencias; medir calidad, fallos de schema/citas,
latencia y coste. Versionar modelo, perfil y parámetros; no convertir razonamiento interno en contrato.

**Gate:** reporte revisado sobre dev y evaluación final held-out, presupuesto y privacidad definidos,
y ADR de selección. Un modelo mayor o un gateway solo se adopta si los resultados lo justifican.

### Incremento 12 — Evaluación pedagógica y siguiente intento

Evaluar `intento_t → intervención → intento_t+1` mediante rúbrica experta y un protocolo versionado
de estudiante simulado. Controlar modelo del simulador, dificultad y condiciones comparadas; incluir
corrección de errores y transferencia cuando el diseño lo permita.

**Gate:** revisión experta, análisis de desacuerdos y límites explícitos de la simulación. Afirmar
efecto educativo real requiere un estudio con estudiantes y política de consentimiento/privacidad;
un resultado simulado no lo sustituye.

### Incremento 13 — Feedback y mantenimiento del dataset

Recoger feedback acotado de estudiantes/docentes con autorización, finalidad y retención definidas.
Un feedback no es automáticamente un gold label. Revisar, desidentificar y promover casos a una
nueva versión; proteger splits dev/held-out/tuning y evitar exportar historiales privados por defecto.

**Gate:** trazabilidad de promoción, prueba de aislamiento/ownership y política de privacidad. No
conceder al docente acceso a conversaciones individuales por el hecho de recibir feedback agregado.

### Incremento 14 — OpenUI opcional

Evaluar `TutorResponse → InteractionSpec validado → componentes permitidos` solo si existen
interacciones que justifican presentación dinámica. El tutor decide la intervención; OpenUI adapta
su representación. No ejecutar React/JavaScript arbitrario generado por el modelo.

**Gate:** ADR, contratos estables, accesibilidad, seguridad, citas visibles y fallback a presentación
explícita. Si no aporta valor medido, posponerlo; no bloquea el piloto.

### Incremento 15 — Preparación del piloto

Cerrar asignatura/nivel, participantes y material; proveedor/modelo evaluados; política de ayuda,
menores/crisis si corresponde, privacidad/retención y soporte. Definir cuotas, presupuestos/cortes,
threat model, staging/producción, OIDC/TLS, monitoreo y restauración real desde backups con RPO/RTO.

**Gate:** E2E con identidad/modelo del despliegue, evaluación final reservada, restauración demostrada,
responsables operacionales y criterios de éxito/parada de un piloto pequeño. Ni pgvector ni OpenUI
son requisitos si no superaron sus gates. Un test de reconstrucción de migraciones no acredita restore.

## 6. Decisiones y mantenimiento

La lista de preguntas abiertas y sus gates se mantiene en [DECISIONS.md](DECISIONS.md), para no
confundirlas con ADR aceptados. El [diseño del harness](DOCTA_HARNESS_ARCHITECTURE.md) conserva
las alternativas y sus discrepancias con el plan. Cualquier reordenamiento debe identificar
qué dependencias cambia y qué evidencia/decisión lo autoriza; conservar la numeración histórica.

Antes de implementar 5 se debe concretar material permitido, responsable de revisión y composición
del dataset; si falta una definición, pueden avanzarse schema/runner con fixtures sintéticas sin
declarar completo el gold. Antes de cada incremento posterior, convertir su gate en criterios
medibles y un alcance revisable; el plan no reemplaza los ADR de contrato o infraestructura.

Registrar cada entrega con archivos, comandos/resultados, versiones evaluadas, limitaciones y
próximo slice. Actualizar esta página y el inventario cuando haya evidencia; conservar Fase 0
y ADRs históricos en sus rutas. No renombrar versiones de prompt/retrieval solo para quitar «phase0».
