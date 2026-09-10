# Documentación de Docta

Fase 0 completada; Fase 1 activa para planificación. El siguiente trabajo de implementación
es el **Incremento 5 — Evaluation Harness + Dataset v0**, todavía pendiente.

| Documento | Función | Cuándo actualizarlo |
| --- | --- | --- |
| [Estado actual](CURRENT_STATE.md) | Qué existe, dónde está y qué evidencia lo respalda. | Cuando cambie la implementación o se registre nueva verificación. |
| [Fase 1: calidad tutorial, retrieval y evaluación](PHASE_1_TUTOR_QUALITY.md) | Alcance activo, criterios del próximo incremento y secuencia condicionada de evolución. | Al aceptar un cambio de alcance o demostrar un criterio. |
| [Arquitectura](DOCTA_ARCHITECTURE.md) | Invariantes, límites del monolito y diseños objetivo, distinguidos de la implementación. | Al cambiar el diseño; decisiones materiales requieren ADR. |
| [Índice de ADR](adr/README.md) | Decisiones específicas aceptadas y su evolución. | Al aceptar, implementar o sustituir una decisión. |
| [Walking skeleton — Fase 0](WALKING_SKELETON.md) | Especificación, incrementos 0–4/2B y evidencia histórica de cierre. | Solo notas históricas explícitas; no agregar backlog de Fase 1. |
| [Reglas del repositorio](../AGENTS.md) | Cómo trabajar y qué garantías conservar. | Cuando cambie la fase o una restricción de ingeniería. |
| [README del proyecto](../README.md) | Instalación, arranque y comandos de verificación. | Cuando cambie la operación reproducible. |

## Operación

- [Ingesta](runbooks/ingestion.md): outbox, worker, Redis Streams y recuperación.
- [Conversaciones](runbooks/conversations.md): proveedor, SSE, fallos y diagnóstico de abstención.
- [Interfaz y OIDC](runbooks/browser-workspace.md): configuración, recorrido docente/estudiante y E2E.

## Cómo conservar el historial

Los ADR 0001–0003 y los resultados fechados de Fase 0 se conservan en sus rutas originales.
Una nueva decisión referencia qué parte sustituye; no convierte una propuesta antigua en
evidencia de implementación. Los resultados de una sesión anterior mantienen su fecha y alcance.
Los textos de planificación aportados por el propietario se sintetizan en Fase 1 y ADR 0004;
sus hipótesis sobre calidad no se tratan como resultados experimentales ni como bibliografía validada.

Para una tarea nueva, leer las reglas, el estado actual, el incremento activo y los ADR relevantes.
La arquitectura objetivo no autoriza construir capacidades fuera de ese incremento.
