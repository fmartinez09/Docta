# Documentación de Docta

**Revisión:** 2026-09-22. Fase 0 completada; Fase 1 activa para planificación. El siguiente
trabajo es el **Incremento 5 — Evaluation Harness + Dataset v0**, todavía pendiente.
La reorganización documental no adopta un agent loop ni cambia el runtime.

## Documentos canónicos

| Documento | Función | Cuándo actualizarlo |
| --- | --- | --- |
| [Estado actual](CURRENT_STATE.md) | Qué existe, dónde está y qué evidencia lo respalda. | Cuando cambie la implementación o se registre nueva verificación. |
| [Fase 1: calidad tutorial, retrieval y evaluación](PHASE_1_TUTOR_QUALITY.md) | Alcance activo, criterios del próximo incremento y secuencia condicionada de evolución. | Al aceptar un cambio de alcance o demostrar un criterio. |
| [Arquitectura](DOCTA_ARCHITECTURE.md) | Límites del monolito, flujos vigentes y fronteras de evolución. | Al cambiar el diseño; decisiones materiales requieren ADR. |
| [Harness](DOCTA_HARNESS_ARCHITECTURE.md) | Distingue evaluación/runtime y reconcilia la propuesta adaptativa con los contratos actuales. | Al resolver una propuesta o implementar una capacidad. |
| [Historia y decisiones](DECISIONS.md) | Cronología, estados y preguntas pendientes con gates. | Al aceptar/rechazar una alternativa o aportar evidencia. |
| [Índice de ADR](adr/README.md) | Decisiones específicas aceptadas y su evolución. | Al aceptar, implementar o sustituir una decisión. |
| [Walking skeleton — Fase 0](WALKING_SKELETON.md) | Especificación, incrementos 0–4/2B y evidencia histórica de cierre. | Solo notas históricas explícitas; no agregar backlog de Fase 1. |
| [Reglas del repositorio](../AGENTS.md) | Cómo trabajar y qué garantías conservar. | Cuando cambie la fase o una restricción de ingeniería. |
| [README del proyecto](../README.md) | Entrada al proyecto, arranque breve y mapa documental. | Cuando cambien el arranque, estado o navegación. |

## Operación

- [Índice de runbooks](runbooks/README.md): alcance, recursos y precauciones.
- [Desarrollo local](runbooks/local-development.md): instalación completa, endpoints, helpers y checks.
- [Ingesta](runbooks/ingestion.md): outbox, worker, Redis Streams y recuperación.
- [Conversaciones](runbooks/conversations.md): proveedor, SSE, fallos y diagnóstico de abstención.
- [Interfaz y OIDC](runbooks/browser-workspace.md): configuración, recorrido docente/estudiante y E2E.

## Fuentes y orden de lectura

Para una tarea: `AGENTS.md` → estado actual → incremento activo → ADR relevantes.
El plan define alcance; un ADR aceptado prevalece sobre un ejemplo de arquitectura. El documento
de harness no sustituye ADR 0004: su loop sigue siendo propuesta. Ante una discrepancia,
registrarla y conservar el contrato menos expansivo hasta una decisión explícita.

Los estados **implementado**, **aceptado**, **planificado**, **propuesto** e **histórico** no son
intercambiables. Sus definiciones y pendientes viven en [DECISIONS.md](DECISIONS.md).

## Referencias e historia

| Referencia | Qué se conserva | Cómo usarla |
| --- | --- | --- |
| [Arquitectura v1.1](DOCTA_ARCHITECTURE_V1.1_REFERENCE.md) | Texto extenso anterior, con revisión del 2026-09-09. | Recuperar motivación y diseños; no ejecutar sus prescripciones como backlog actual. |
| [Harness original](DOCTA_HARNESS_ARCHITECTURE_REFERENCE.md) | Propuesta importada como `DOCTA_HARNESS_ARCHITECTURE (2).md`, con bibliografía y ejemplos. | Consultar alternativas; la versión canónica explica lo no adoptado. |
| [Walking skeleton](WALKING_SKELETON.md) | Checklists y verificaciones de Fase 0. | Evidencia técnica histórica, no pruebas de calidad pedagógica. |

Las referencias conservan su contenido original tras una cabecera de contexto. Sus fechas de
consulta bibliográfica y límites propuestos no se actualizan como si se hubieran revalidado hoy.
Las rutas canónicas `DOCTA_ARCHITECTURE.md` y `DOCTA_HARNESS_ARCHITECTURE.md` son las entradas actuales;
el sufijo de descarga `(2)` ya no identifica el documento mantenido.

## Cómo conservar el historial

Los ADR 0001–0003 y los resultados fechados de Fase 0 se conservan en sus rutas originales.
Una nueva decisión referencia qué parte sustituye; no convierte una propuesta antigua en
evidencia de implementación. Los resultados de una sesión anterior mantienen su fecha y alcance.
Los textos de planificación aportados por el propietario se sintetizan en Fase 1 y ADR 0004;
sus hipótesis sobre calidad no se tratan como resultados experimentales ni como bibliografía validada.

No modificar decisiones antiguas para simular una aprobación nueva. Añadir notas fechadas y,
cuando corresponda, un ADR que enumere las partes sustituidas. No copiar trazas privadas ni
conversaciones de planificación completas al dataset sin autorización/revisión.
