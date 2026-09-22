# Runbooks de Docta

Los procedimientos describen capacidades existentes. Los registros fechados son históricos;
ejecutar un comando documentado hoy no implica que se haya ejecutado durante la última revisión.

| Necesidad | Procedimiento | Recursos afectados al ejecutarlo |
| --- | --- | --- |
| Instalar, arrancar y comprobar el checkout | [Desarrollo local](local-development.md) | Entorno local; tests usan servicios y recursos aislados. |
| Diagnosticar carga/indexación y worker | [Ingesta](ingestion.md) | Metadatos de jobs; recuperación conserva volúmenes y versiones. |
| Configurar modelo, SSE, timeouts y abstención | [Conversaciones](conversations.md) | API/modelo configurados; no reintenta automáticamente generaciones terminadas. |
| Configurar OIDC y recorrer la UI | [Workspace](browser-workspace.md) | Origen/callback configurados y curso autorizado; E2E usa sus propios recursos. |

No hay un comando de evaluación de calidad implementado todavía. Su futuro runbook forma parte
de los criterios de [Incremento 5](../PHASE_1_TUTOR_QUALITY.md); no confundir pytest con ese benchmark.

Antes de operar, confirmar entorno, identidad, puertos y destino. No imprimir `.env`, tokens,
prompts ni respuestas en logs; no borrar volúmenes ni resetear desarrollo para resolver un test.
Los comandos de test que reinician dependencias se ejecutan serialmente, nunca contra desarrollo.
