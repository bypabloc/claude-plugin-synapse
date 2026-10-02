# Componentes de un Plugin a Fondo

> Análisis exhaustivo y detallado de cada componente ejecutable soportado en un plugin de Claude Code: Skills, Subagentes, Hooks, Servidores MCP, Servidores LSP, Monitores, Binarios, Canales y Personalizaciones.

---

## 1. Skills (`skills/<name>/SKILL.md`)

Los **Skills** son las unidades primarias de instrucción y comandos personalizados. Un skill combina instrucciones en lenguaje natural, metadatos estructurados (frontmatter YAML) y opcionalmente archivos de apoyo.

### Estructura de Directorio de un Skill
```text
skills/
└── code-review/
    ├── SKILL.md            <-- Instrucciones principales y frontmatter
    ├── checklist.json      <-- Datos complementarios
    └── examples/           <-- Ejemplos de referencia cargables por el skill
        └── good-pr.md
```

### Especificación de Frontmatter en `SKILL.md`

```markdown
---
name: code-review
description: Revisa pull requests y cambios en código buscando vulnerabilidades y calidad.
disable-model-invocation: false
argument-hint: "[branch-or-commit]"
---

Instrucciones detalladas que Claude ejecutará al invocar el skill...
```

| Campo Frontmatter | Tipo | Propósito |
| :--- | :--- | :--- |
| `name` | String | Reemplaza el nombre de la carpeta en el comando. El comando final siempre antepone el prefijo del plugin: `/<plugin>:<name>`. |
| `description` | String | Texto inyectado en el context window en cada turno para que Claude decida si invocarlo automáticamente. |
| `disable-model-invocation` | Boolean | Si es `true`, **solo el usuario** puede ejecutarlo escribiendo `/<plugin>:<skill>`. El modelo no lo invocará por su cuenta. Ahorra tokens en el system prompt. |
| `argument-hint` | String | Pista visual de argumentos mostrada en el autocompletado (ej. `[archivo] [modo]`). |

> [!NOTE]
> **Archivos de Soporte:** A diferencia de los comandos clásicos (`commands/*.md`), un skill reside en su propia carpeta, lo que le permite empaquetar plantillas, esquemas JSON y scripts que Claude puede leer con `view_file` cuando el skill se active.

---

## 2. Subagentes Especializados (`agents/*.md`)

Un subagente es un asistente independiente con su propio prompt del sistema, herramientas restringidas y **su propia ventana de contexto aislada**. Los subagentes permiten dividir tareas complejas (ej. auditoría de seguridad, refactorización masiva) sin contaminar la conversación principal.

### Definición de un Subagente: `agents/security-reviewer.md`

```markdown
---
name: security-reviewer
description: Especialista en seguridad. Audita código en busca de inyecciones SQL, XSS, secretos expuestos y fallos OWASP.
model: sonnet
effort: high
maxTurns: 15
tools:
  - ReadFile
  - Grep
  - Glob
disallowedTools:
  - WriteFile
  - EditFile
  - Bash
isolation: "worktree"
color: "red"
---

Eres un Auditor Principal de Seguridad de Aplicaciones.
Analiza exhaustivamente los cambios realizados en el proyecto. 
Prioriza:
1. Vulnerabilidades de inyección (SQL, comandos, LDAP).
2. Fugas de credenciales y secretos hardcodeados.
3. Deserialización insegura y validación de tipos en APIs públicas.

Genera un informe objetivo con severidades: [CRITICAL], [HIGH], [MEDIUM], [LOW].
```

### Nombres y Rutas Recursivas de Subagentes

Claude Code escanea subdirectorios dentro de `agents/` de manera recursiva:
- `agents/tester.md` en plugin `dev-tools` -> `dev-tools:tester`.
- `agents/audit/compliance.md` en plugin `dev-tools` -> `dev-tools:audit:compliance`.
- Invocación explícita por el usuario: `@agent-dev-tools:security-reviewer Haz una auditoría completa`.

### Campos Frontmatter Soportados vs. Ignorados en Plugins

| Soportados en Subagentes | Ignorados en Plugins (Definir en el Manifiesto Principal) |
| :--- | :--- |
| `name`, `description`, `model` | `permissionMode` (Hereda el de la sesión) |
| `effort` (`low`, `medium`, `high`) | `hooks` (Los agentes no pueden registrar hooks privados) |
| `maxTurns` (Límite entero de turnos) | `mcpServers` (Los servidores MCP se definen en `.mcp.json`) |
| `tools` y `disallowedTools` | `initialPrompt` |
| `isolation: "worktree"` (Aísla en Git worktree) | |
| `memory` (`true`/`false`), `color` | |

---

## 3. Hooks y Ciclo de Vida (`hooks/hooks.json`)

Los hooks permiten interceptar momentos clave del ciclo de vida de Claude Code para aplicar linters, validar permisos, inyectar contexto o bloquear operaciones riesgosas.

### Topología de Eventos Soportados

```
                     +---------------------------------------+
                     |         Inicio de Sesión              |
                     |         Event: SessionStart           |
                     +-------------------+-------------------+
                                         |
                                         v
                     +---------------------------------------+
                     |       Mensaje del Usuario             |
                     |       Event: UserPromptSubmit         |
                     +-------------------+-------------------+
                                         |
                                         v
                     +---------------------------------------+
                     |       Antes de Usar Herramienta       |
                     |       Event: PreToolUse (Write/Edit)  |
                     +-------------------+-------------------+
                                         |
                                         v
                     +---------------------------------------+
                     |       Después de Usar Herramienta     |
                     |       Event: PostToolUse (Write/Edit) |
                     +-------------------+-------------------+
                                         |
                                         v
                     +---------------------------------------+
                     |       Fin de Turno / Tarea            |
                     |       Event: Stop / SubagentStop      |
                     +---------------------------------------+
```

### Configuración Declarativa: `hooks/hooks.json`

```json
{
  "description": "Quality gates y refuerzo de políticas de código",
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/scripts/session-start.cjs\"",
            "timeout": 10
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/scripts/prompt-guard.cjs\"",
            "timeout": 5
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/validate-bash.sh\""
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/format-file.sh\""
          }
        ]
      }
    ]
  }
}
```

### Formato de Entrada y Salida JSON del Protocolo de Hooks

Los scripts invocados por hooks reciben la carga útil a través de `stdin` en formato JSON y comunican resultados mediante `stdout` en formato JSON.

#### 1. Inyección de Contexto en `SessionStart`
Permite cargar directrices dinámicas, leer bases de datos locales o reglas de estilo:

```json
// Salida stdout del script:
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "REGLA OBLIGATORIA DEL PROYECTO: Utilizar siempre TypeScript 5.3+ estricto y pruebas TDD."
  }
}
```

#### 2. Refuerzo de Instrucciones en `UserPromptSubmit`
Inyecta un mensaje del sistema junto a cada turno del usuario (patrón utilizado por `claude-plugin-spanish-latam` para evitar desviaciones de dialecto):

```json
// Salida stdout del script:
{
  "systemMessage": "Mantén respuestas objetivas, técnicas y concisas en español neutro."
}
```

#### 3. Intercepción y Bloqueo en `PreToolUse`
Si el script detecta un comando prohibido (ej. `rm -rf /` o un push no autorizado a master), puede abortar la acción saliendo con código distinto de cero o emitiendo un bloqueo estructurado:

```bash
#!/bin/bash
# scripts/validate-bash.sh
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

if echo "$COMMAND" | grep -qE "git push.*(master|main)"; then
  echo "Error: Está prohibido hacer push directo a ramas protegidas. Crea una rama feature/*." >&2
  exit 1
fi

exit 0
```

---

## 4. Servidores MCP (`.mcp.json`)

El Model Context Protocol (MCP) conecta a Claude con herramientas externas y bases de datos. Los plugins pueden empaquetar servidores MCP locales (ej. scripts en Node.js o Python) o vincular servidores remotos.

### Configuración en `.mcp.json`

```json
{
  "mcpServers": {
    "git-tools": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/servers/git-mcp.js"],
      "env": {
        "PROJECT_PATH": "${CLAUDE_PROJECT_DIR}",
        "AUTH_TOKEN": "${user_config.api_token}"
      }
    }
  }
}
```

### Convenciones de Nombres de Servidores y Herramientas MCP

Para garantizar que múltiples plugins no colisionen en sus herramientas:
- **Nombre del Servidor en `/mcp`:** `plugin:<plugin-name>:<server-name>` (ej. `plugin:dev-tools:git-tools`).
- **Identificador de Herramienta:** `mcp__plugin_<plugin>_<server>__<tool>` (ej. `mcp__plugin_dev_tools_git_tools__create_pr`).
- Este identificador canónico es el que debe usarse en políticas de permisos y en el campo `matcher` de los hooks.

### Empaquetado MCPB (`.mcpb` / `.dxt`)

Los plugins pueden referenciar bundles binarios precompilados de MCP (`.mcpb`):

```json
{
  "name": "cloud-integrator",
  "mcpServers": "./bin/aws-mcp.mcpb"
}
```
Claude Code desempaqueta automáticamente el bundle en `.mcpb-cache/` y ejecuta el servidor de manera aislada.

---

## 5. Servidores de Lenguaje LSP (`.lsp.json`)

Los plugins de inteligencia de código proporcionan diagnósticos de compilación, saltos a definición e inspección de símbolos en tiempo real.

```json
{
  "typescript": {
    "command": "typescript-language-server",
    "args": ["--stdio"],
    "extensionToLanguage": {
      ".ts": "typescript",
      ".tsx": "typescriptreact",
      ".js": "javascript",
      ".jsx": "javascriptreact"
    },
    "restartOnCrash": true,
    "maxRestarts": 3
  }
}
```

> [!IMPORTANT]
> Los servidores LSP deben enviar todos sus registros de depuración a `stderr`. Claude Code interpreta `stdout` estrictamente como mensajes del protocolo LSP. Si el servidor escribe texto plano en `stdout`, se considerará un error de protocolo y se forzará la desconexión.

---

## 6. Monitores de Fondo (`monitors/monitors.json`)

Un monitor es un proceso en segundo plano que corre durante toda la sesión interactiva y transmite alertas o logs en tiempo real hacia Claude Code:

```json
[
  {
    "name": "dev-server-errors",
    "command": "tail -F ./tmp/logs/app.log",
    "description": "Monitorea errores de tiempo de ejecución del servidor local",
    "when": "always"
  },
  {
    "name": "build-watcher",
    "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/watch-build.sh\"",
    "description": "Monitorea fallos en la compilación de TypeScript",
    "when": "on-skill-invoke:build"
  }
]
```

- `when: "always"`: Se activa inmediatamente al abrir la sesión interactiva.
- `when: "on-skill-invoke:<skill>"`: Se activa únicamente la primera vez que el usuario ejecuta ese skill específico.

---

## 7. Ejecutables en el PATH (`bin/`)

Cualquier archivo ejecutable (`chmod +x`) colocado en la carpeta `bin/` del plugin es agregado automáticamente al `$PATH` de la herramienta Bash durante la sesión.

```bash
#!/bin/bash
# bin/df-format
exec npx prettier --write "$@"
```

Una vez habilitado el plugin, Claude puede invocar `df-format src/file.ts` directamente desde la herramienta Bash sin necesidad de especificar rutas complejas.

> [!WARNING]
> La carpeta `bin/` del plugin tiene **menor prioridad** que el `$PATH` del usuario. Un plugin no puede sobreescribir ni secuestrar comandos del sistema como `git`, `rm` o `node`.

---

## 8. Configuración Interactiva (`userConfig`)

Permite a los plugins solicitar parámetros al usuario sin obligarlo a editar archivos JSON manuales:

```json
{
  "userConfig": {
    "jira_host": {
      "type": "string",
      "title": "Jira Host",
      "description": "Dominio base de la instancia de Jira (ej. https://empresa.atlassian.net)"
    },
    "jira_token": {
      "type": "string",
      "title": "API Token de Jira",
      "description": "Token personal generado en Atlassian",
      "sensitive": true,
      "required": true
    },
    "linter_severity": {
      "type": "string",
      "title": "Nivel de severidad",
      "description": "Nivel de severidad para reportar warnings",
      "options": ["error", "warning", "info"],
      "default": "warning"
    }
  }
}
```

- Si `sensitive: true`, el valor se almacena en el gestor seguro de credenciales del sistema operativo y nunca en texto plano en `settings.json`.
- Para configurar valores desde scripts o terminal headless:
  ```bash
  claude plugin configure dev-tools@my-market --values-stdin < config.json
  ```
