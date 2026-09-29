Necesitas tener Docker funcionando y la CLI de Dev Containers instalada. No se cual corresponda a tu gestor de paquetes.

Existe la alternativa vía npm:

```bash
npm install -g @devcontainers/cli
```

y también si fuera necesario agregarla al `PATH` 

```bash
export PATH="$HOME/.devcontainers/bin:$PATH"
```

Luego, desde la raíz del repo:

```bash
devcontainer up --workspace-folder .
```

Eso construye/inicializa el ambiente definido en .devcontainer

Se debería levanta un contenedor:

- Dev Container principal del workspace con un codename friendly;


No necesitas VS Code. Puedes abrir el repo directamente con tu Neovim normal (o no sé que estabas usando):

```bash
nvim .
```

y en otra terminal entrar al entorno de desarrollo:

```bash
devcontainer exec --workspace-folder . bash
```

Desde esa shell estás dentro del Dev Container.

Si usas vscode usa simplemente:

```bash
code .
```

Espera a que termine la inicialización (mensaje Done. Press any key to close the terminal) 

Luego, para levantar por primera vez todo

```bash
npm run dev:setup
```

Configuración recomendada:

```text

Administrator credentials for this new instance: change/reuse [change]: change
New ZITADEL administrator login/email [developer-5d0cd9f0@docta.local]: el que quieras, usa el recomendado
New ZITADEL administrator password: su password
Confirm new ZITADEL administrator password: 
```

ahora el provider 

```bash
npm run dev:configure
``` 

```text
Tutor service [keep]: google

Full Chat Completions endpoint:
https://generativelanguage.googleapis.com/v1beta/openai/chat/completion

Model ID / server alias:
gemini-3.1-flash-lite

Tutor API key:
su API key

Tutor timeout seconds (1-179) [175]:
45

Message deadline (5-180s, above tutor timeout) [180]:
90

Completion budget (128-8000) [8000]: -> es el contexto del llm
2000
```

Depues de esot tendrías tres contnedores:

- docta-dev con PostgreSQL, Redis, etc.;
- docta-dev-iam con Zitadel.

Después puedes levantar Docta completo con:

```bash
npm run dev:all
```

y debería quedar disponible en:

```text
http://127.0.0.1:13000
```


Para crear el usuario en ZITADEL, lo mismo que la vez anterior.
http://localhost:18080/ui/console
Usuario: el que elegiste en consola
Contraseña: la que ingresaste

Luego en users -> new -> formulario -> 
Set an initial password for the User y le asignas password.


Si haces los commits desde Linux host, usa tu configuración Git normal.

Si quieres ejecutar Git desde dentro del Dev Container, entonces tendrás que configurar ahí:

```bash
git config user.name "Tu Nombre"
git config user.email "tu@email"
```

Los datos persistentes, el historial de chats etc quedan en los volúmenes de Docker.

Si detienes los contenedores puedes volver a iniciarlos posteriormente. Si los eliminas completamente, tendrás que reconstruir el Dev Container y posiblemente volver a ejecutar:

```bash
npm run dev:configure
```

Si usas algún agente, es recomendable que viva dentro del worktree
devcontainer exec --workspace-folder . bash
Claude, Opencode, etc. 

