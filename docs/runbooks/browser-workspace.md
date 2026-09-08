# Prueba completa de la interfaz

Estado local verificado el 2026-09-08: web en `http://127.0.0.1:3100`, API en el puerto 8000,
worker activo y base migrada hasta 0008. ZITADEL está iniciado y el flujo de Docta llega a su
pantalla de acceso. Unsloth pasó una prueba sintética con una cita válida usando el perfil
`DOCTA_TUTOR_PROVIDER=unsloth` y un presupuesto de 512 tokens. Reinicia la API después de
cambiar esa configuración. El callback de
la web debe usar el puerto **3100**. Los ejemplos de instalación limpia usan 3000; al usar el `.env` local actual,
sustituye 3000 por 3100 en la dirección, el callback y el comando de arranque de la web.

La interfaz incluye acceso OIDC, creación de cursos, carga directa del PDF, seguimiento de
procesamiento, publicación y conversaciones con estados y citas. El rol lo determina la
membresía guardada en el servidor. El docente también puede probar el tutor de su propio curso.

## 1. Preparar configuración

Conserva tu `.env` existente. Configura OIDC y las tres variables `DOCTA_TUTOR_*` del proveedor
según [conversations.md](conversations.md). Unsloth debe estar servido mediante un endpoint
compatible con el contrato Chat Completions descrito allí; el nombre del modelo por sí solo
no garantiza soporte de JSON Schema ni de los parámetros de generación.

Añade a la aplicación pública OIDC de ZITADEL la Redirect URI exacta
`http://127.0.0.1:3000/auth/callback`. Mantén `http://127.0.0.1:8765/callback` si usas el helper.
Selecciona Authorization Code, PKCE y access tokens JWT. Copia su Client ID a
`DOCTA_WEB_OIDC_CLIENT_ID`, o reutiliza el existente `DOCTA_DEV_OIDC_CLIENT_ID`.

En `.env`, configura `DOCTA_WEB_ORIGIN=http://127.0.0.1:3000` y un
`DOCTA_WEB_SESSION_SECRET` aleatorio de al menos 32 caracteres. Para generar uno localmente:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Guárdalo solo en `.env`. Next.js carga este archivo desde la raíz; no uses prefijos
`NEXT_PUBLIC_` para secretos. Usa siempre `127.0.0.1:3000`, porque `localhost` es otro origen.

Si otros proyectos ocupan esos puertos, usa valores libres y mantén sincronizados
`POSTGRES_PORT` con `DOCTA_DATABASE_URL`, `REDIS_PORT` con `DOCTA_REDIS_URL`, y
`MINIO_API_PORT` con las URLs S3 y de salud. La configuración local de esta verificación usa
PostgreSQL **15432**, Redis **16379**, MinIO **19000** (consola **19001**) y web **3100** para
convivir con Langfuse. En ese caso registra `http://127.0.0.1:3100/auth/callback` en ZITADEL,
abre `http://127.0.0.1:3100` y sustituye el comando de la web por:

```powershell
npm run dev:web:local
```

Los ejemplos siguientes mantienen los puertos predeterminados de `.env.example`.
El script `dev:web:local` incluye los parámetros de Next.js y evita depender del reenvío
de argumentos de npm desde PowerShell.

## 2. Iniciar servicios y migrar

Si el stack local de ZITADEL ya está creado pero detenido, arráncalo conservando sus datos:

```powershell
docker compose -p zitadel -f zitadel-compose/docker-compose.yml start
```

El comando usa la configuración y los volúmenes existentes de `zitadel-compose`; conserva su
`.env`. Comprueba que `http://localhost:8080/.well-known/openid-configuration` responde.
Si accedes a Docta por `localhost`, el inicio de sesión te redirige al origen configurado antes
de crear su cookie PKCE, para que coincida con el dominio del callback.

Desde la raíz, con API y workers detenidos mientras migras:

```powershell
npm ci
uv sync
docker compose --env-file .env -f infra/compose.yaml up -d --wait
uv run alembic -c apps/api/alembic.ini upgrade head
```

La migración 0007 crea conversaciones, mensajes, evidencia y citas. La 0008 añade el registro
de reintentos de creación de curso para evitar duplicados. Ambas son aditivas.

En tres terminales separadas:

```powershell
uv run uvicorn docta_api.main:app --app-dir apps/api/src --env-file .env --host 127.0.0.1 --port 8000
```

```powershell
uv run python -m docta_api.worker
```

```powershell
npm run dev:web
```

## 3. Recorrido manual

1. Abre `http://127.0.0.1:3000` y pulsa **Entrar a mi espacio**. Inicia sesión en ZITADEL.
2. Pulsa **Crear curso**, escribe un nombre y confirma. Aparece el espacio docente.
3. Selecciona un PDF digital con texto seleccionable, de hasta 10 MB, y pulsa **Subir PDF**.
   Espera **Listo para publicar**. Si queda en espera, revisa que el worker esté ejecutándose.
4. Pulsa **Publicar material**. Debe aparecer **Material publicado**. El tutor solo utiliza ese
   corpus activo, aunque hayas cargado otros PDFs.
5. Escribe una pregunta con términos que estén en el PDF. Observa el estado pendiente y luego
   una respuesta pedagógica. Abre su cita para comprobar documento, página, texto y versión.
6. Recarga la página. La pregunta y la respuesta deben seguir presentes. También puedes recargar
   durante el estado pendiente: el servidor continúa y la interfaz recupera el resultado.
7. Pregunta por un tema ausente. Debe aparecer **Evidencia insuficiente**, sin citas inventadas.
8. Para probar un fallo real, detén el servidor del modelo y pregunta por términos presentes en
   el PDF. Debe aparecer el fallo con la pregunta conservada. Reinicia el modelo; el siguiente
   envío es una nueva pregunta. **Recuperar envío** reconcilia una entrega interrumpida usando
   su misma clave; no vuelve a generar una respuesta ya terminada.
9. Para probar el rol estudiante, usa otra cuenta que ya tenga una membresía `student` en ese
   curso. Verá el tutor y sus propias conversaciones. Este incremento no incluye inscripción
   ni invitaciones; sin membresías aparece una pantalla de cursos vacía. El test automatizado
   provisiona esa membresía únicamente en su base aislada.
10. Pulsa **Cerrar sesión**. Docta elimina su sesión local; la sesión global de ZITADEL puede
    seguir activa. El acceso al proxy vuelve a requerir autenticación.

## 4. Prueba automatizada reproducible

Si Windows reserva el puerto de pruebas 59000, selecciona otro puerto libre en la misma
terminal antes de ejecutar Compose y pytest: `$env:DOCTA_TEST_MINIO_PORT = "19500"`.
Este ajuste afecta solo al MinIO de pruebas; el host y los recursos siguen aislados.

```powershell
docker compose -f infra/compose.test.yaml up -d --wait
npm run build:web
npx playwright install chromium
uv run pytest tests/e2e -q
```

Si ya tienes Microsoft Edge instalado en Windows puedes omitir la descarga y ejecutar antes:

```powershell
$env:DOCTA_E2E_BROWSER_CHANNEL = "msedge"
```

El test inicia API, worker y web en puertos libres, realiza login PKCE, crea un curso desde el
navegador, sube/publica un PDF y entra con otra identidad estudiante. Comprueba citas, recarga
durante generación, abstención, fallo persistido, autorización, CSRF, cookie HttpOnly, logout y
ancho móvil. Usa servicios reales y dobles deterministas de identidad/modelo solo en tests.
No llama a tu modelo ni modifica tu base de desarrollo. Guarda capturas en el directorio temporal
del test. No ejecutes suites de integración concurrentes: otros tests reinician estos servicios.

En Windows, si hay problemas de permisos con temporales antiguos:

```powershell
$doctaTestTemp = Join-Path $env:TEMP ('docta-tests-' + [guid]::NewGuid().ToString('N'))
uv run pytest -p no:cacheprovider --basetemp=$doctaTestTemp
```

## Límites actuales

Los listados muestran como máximo los 100 cursos, documentos o conversaciones más recientes;
el historial de mensajes sí se recorre por páginas. No hay OCR, inscripción, visor PDF embebido,
edición de mensajes ni evaluación pedagógica automatizada. Las citas muestran su fragmento
auditable. Una cita válida prueba procedencia; no demuestra por sí sola la calidad de la respuesta.

La sesión dura como máximo una hora, o menos si el token vence antes; se requiere volver a
iniciar sesión. El borrador y la clave de un envío todavía no aceptado no sobreviven una recarga.
