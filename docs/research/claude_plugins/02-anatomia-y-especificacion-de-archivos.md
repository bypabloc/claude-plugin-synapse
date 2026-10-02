# Anatomía y Especificación de Archivos de un Plugin

> Referencia técnica completa del manifiesto `plugin.json`, topología de carpetas, reglas de validación estricta y variables de entorno del runtime.

---

## 1. Topología Estándar de Archivos (Standard Layout)

Un plugin de Claude Code organiza sus componentes en una estructura de carpetas estandarizada a partir de la raíz del plugin:

```text
nombre-del-plugin/
├── .claude-plugin/
│   └── plugin.json             <-- OBLIGATORIO para metadatos y userConfig
├── skills/                     <-- Skills modernos con carpetas independientes
│   └── review/
│       ├── SKILL.md            <-- Invocable como /nombre-del-plugin:review
│       └── reference.json      <-- Archivos de soporte accesibles por el skill
├── commands/                   <-- Comandos legados (.md planos, preferir skills/)
│   └── quick-check.md          <-- Invocable como /nombre-del-plugin:quick-check
├── agents/                     <-- Definiciones de subagentes especializados
│   ├── security-auditor.md     <-- Invocable con @agent-nombre-del-plugin:security-auditor
│   └── qa/
│       └── tester.md           <-- Invocable como nombre-del-plugin:qa:tester
├── hooks/
│   └── hooks.json              <-- Configuración declarativa de hooks
├── scripts/                    <-- Scripts auxiliares invocados por hooks (convención)
│   ├── post-edit-lint.sh
│   └── session-init.cjs
├── bin/                        <-- Binarios ejecutables inyectados en el $PATH
│   └── custom-linter
├── monitors/
│   └── monitors.json           <-- Tareas persistentes en background
├── output-styles/
│   └── strict.md               <-- Modifica el tono de Claude (/output-style)
├── themes/
│   └── dark-minimal.json       <-- Modifica colores de interfaz (/theme)
├── workflows/
│   └── audit-pipeline.js       <-- Orquestación de múltiples subagentes
├── settings.json               <-- Ajustes por defecto (agent, subagentStatusLine)
├── .mcp.json                   <-- Servidores MCP estándar
└── .lsp.json                   <-- Servidores Language Server Protocol
```

> [!CAUTION]
> **Regla de oro de la carpeta `.claude-plugin/`:**
> Dentro de `.claude-plugin/` **ÚNICAMENTE** debe residir `plugin.json`. Si colocas las carpetas `skills/`, `hooks/`, o `agents/` dentro de `.claude-plugin/`, el motor de descubrimiento de Claude Code **no las cargará** y generará advertencias de validación.

---

## 2. Especificación Completa de `.claude-plugin/plugin.json`

El archivo de manifiesto es un JSON que valida frente al validador oficial `claude plugin validate`.

### Ejemplo de Manifiesto Exhaustivo

```json
{
  "$schema": "https://anthropic.com/claude-code/plugin.schema.json",
  "name": "dev-toolbox",
  "displayName": "Developer Toolbox Pro",
  "version": "1.3.0",
  "description": "Herramientas de formateo, revisión estricta y servidores MCP internos",
  "author": {
    "name": "Pablo Contreras",
    "email": "pacg1991@gmail.com",
    "url": "https://github.com/bypabloc"
  },
  "homepage": "https://github.com/bypabloc/dev-toolbox",
  "repository": "https://github.com/bypabloc/dev-toolbox.git",
  "license": "MIT",
  "keywords": ["linter", "security", "git", "tools"],
  "defaultEnabled": true,
  "dependencies": [
    "git-tools@claude-plugins-official",
    {
      "name": "secrets-scanner",
      "marketplace": "corporate-plugins",
      "version": "^2.0.0"
    }
  ],
  "metadata": {
    "internalTeamId": "core-eng-01"
  },
  "skills": ["./extra-skills/"],
  "commands": {
    "audit": {
      "source": "./commands/audit.md",
      "description": "Ejecuta auditoría rápida de seguridad",
      "argumentHint": "[scope]"
    }
  },
  "agents": ["./custom-agents/reviewer.md"],
  "hooks": "./config/alternate-hooks.json",
  "mcpServers": {
    "internal-db": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/servers/db.js"],
      "env": {
        "DB_TOKEN": "${user_config.db_token}"
      }
    }
  },
  "lspServers": "./.lsp.json",
  "outputStyles": "./output-styles/",
  "experimental": {
    "monitors": "./monitors/monitors.json",
    "themes": "./themes/",
    "evals": "./evals/"
  },
  "userConfig": {
    "api_endpoint": {
      "type": "string",
      "title": "API Endpoint",
      "description": "URL base del servicio interno",
      "default": "https://api.empresa.internal"
    },
    "db_token": {
      "type": "string",
      "title": "Database Access Token",
      "description": "Token secreto de lectura en base de datos",
      "sensitive": true,
      "required": true
    },
    "environment": {
      "type": "string",
      "title": "Entorno de Ejecución",
      "description": "Selecciona el entorno objetivo",
      "options": ["development", "staging", "production"],
      "default": "development"
    }
  }
}
```

### Tabla de Campos del Manifiesto

| Campo | Tipo | Requerido | Descripción |
| :--- | :--- | :--- | :--- |
| `name` | String | **Sí** | Identificador del plugin. Debe ser `kebab-case`. Actúa como prefijo para skills y agentes. |
| `displayName` | String | No | Nombre visible en interfaces gráficas y comandos interactivos. |
| `version` | String | Recomendado | Versión semántica del plugin (`MAJOR.MINOR.PATCH`). |
| `description` | String | Recomendado | Descripción mostrada al listar y buscar plugins en `/plugin`. |
| `author` | Objeto | Recomendado | Objeto con `name` (obligatorio), `email` (opcional) y `url` (opcional). |
| `homepage` | String (URL) | No | Enlace a la documentación oficial del plugin. Debe ser una URL válida. |
| `repository` | String (URL) | No | Enlace al repositorio de código fuente (GitHub, GitLab, etc.). |
| `license` | String | No | Identificador SPDX válido (ej. `MIT`, `Apache-2.0`, `UNLICENSED`). |
| `keywords` | Array[String] | No | Palabras clave para descubrimiento y categorización en marketplaces. |
| `defaultEnabled`| Boolean | No | Si es `true` (por defecto), el plugin se activa automáticamente al instalarse. |
| `dependencies` | Array | No | Plugins requeridos para el funcionamiento de este plugin. |
| `settings` | Objeto | No | Ajustes de sesión predeterminados. Solo aplican `agent` y `subagentStatusLine`. |
| `userConfig` | Objeto | No | Opciones configurables que Claude Code solicita de forma interactiva. |
| `skills` | Path o Array | No | Directorios adicionales de skills. Se suman al escaneo predeterminado de `skills/`. |
| `commands` | Path o Objeto | No | Reemplaza el escaneo de `commands/`. Permite mapeos inline o rutas a `.md`. |
| `agents` | Path o Array | No | Reemplaza el escaneo de `agents/`. Solo acepta archivos `.md`. |
| `hooks` | Path o Objeto | No | Rutas a archivos JSON u objetos inline de hooks. Se fusiona con `hooks/hooks.json`. |
| `mcpServers` | Path o Objeto | No | Servidores MCP inline, archivos `.json` o paquetes `.mcpb`/`.dxt`. Se fusiona con `.mcp.json`. |
| `lspServers` | Path o Objeto | No | Servidores Language Server Protocol. Se fusiona con `.lsp.json`. |
| `outputStyles` | Path o Array | No | Directorios o archivos de estilos de respuesta. Reemplaza `output-styles/`. |
| `workflows` | Path o Array | No | Archivos `.js` de flujos orquestados multi-agente. |
| `experimental` | Objeto | No | Contenedor para `monitors`, `themes` y `evals`. |

---

## 3. Reglas Estrictas de Nomenclatura y Nombres Reservados

Claude Code aplica validaciones de seguridad deterministas sobre el campo `name`:

1. **Formato:** Exclusivamente caracteres alfanuméricos en minúsculas y guiones (`kebab-case`). No se permiten espacios, arrobas (`@`), dos puntos (`:`), barras diagonales (`/`, `\`) ni caracteres de control Unicode.
2. **Nombres Reservados de Anthropic (Error Bloqueante):**
   - No puede comenzar con: `claude-`, `anthropic-`, `anthropics-`, ni `cc-plugin-`.
   - No puede ser idéntico a: `claude`, `anthropic`, `anthropics`, `claude-code`, ni `claude-mods`.
   - No puede contener la palabra `official` junto a `claude` o `anthropic` (ej. `official-claude-tools`).
3. **Nombres Reservados del Sistema de Carga:**
   - `inline` (reservado para plugins cargados por `--plugin-dir`).
   - `builtin` (reservado para plugins del núcleo del sistema).
   - `skills-dir` (reservado para plugins en `~/.claude/skills/`).
   - `synced` (reservado para plugins sincronizados desde la nube).

---

## 4. Reglas de Rutas (Path Rules) y Seguridad

- **Prefijo Obligatorio:** Toda ruta declarada en el manifiesto debe comenzar explícitamente con `./` (ejemplo: `"./skills/"` o `"./scripts/format.sh"`).
- **Prevención de Path Traversal:** Cualquier ruta que contenga secuencias `..` o intente resolver fuera del directorio raíz del plugin es rechazada inmediatamente por `claude plugin validate` y el cargador de la sesión.
- **Rutas a la raíz:** Solo `skills` admite `"."` o `"./"` para designar que un `SKILL.md` reside directamente en la raíz del plugin.

---

## 5. Variables de Entorno del Runtime de Plugins

Cuando un hook, servidor MCP, servidor LSP o script del plugin se ejecuta, Claude Code inyecta automáticamente variables de entorno fundamentales:

| Variable | Resolución en Tiempo de Ejecución | Propósito Principal |
| :--- | :--- | :--- |
| **`${CLAUDE_PLUGIN_ROOT}`** | Ruta absoluta del directorio de instalación de la versión activa del plugin. | Invocar scripts, acceder a archivos de configuración y cargar módulos empaquetados. **Nota:** No escribir datos mutables aquí; se destruye al actualizar. |
| **`${CLAUDE_PLUGIN_DATA}`** | `~/.claude/plugins/data/<sanitized-plugin-id>/` | Almacenamiento persistente que sobrevive a actualizaciones del plugin. Usado para `node_modules`, entornos virtuales de Python (`.venv`), cachés SQLite o bases vectoriales locales. |
| **`${CLAUDE_PROJECT_DIR}`** | Ruta absoluta de la raíz del proyecto donde el usuario abrió Claude Code. | Acceso a archivos del código fuente del usuario, configs del proyecto y `.git/`. |
| **`${user_config.<KEY>}`** | Valor ingresado por el usuario para una opción definida en `userConfig`. | Inyección directa en argumentos (`args`), configuración de servidores MCP/LSP o cuerpo de skills. |
| **`CLAUDE_PLUGIN_OPTION_<KEY>`** | Variable de entorno exportada al proceso del hook con `<KEY>` en mayúsculas. | Acceso seguro a valores configurables desde scripts Bash o Node.js sin riesgo de inyección de comandos en shell. |

### Regla Crítica de Cuoteo en Comandos Shell

Cuando un hook ejecuta un comando en modo shell (sin array de argumentos `args`), la variable `${CLAUDE_PLUGIN_ROOT}` debe **siempre envolverse en comillas dobles** para soportar rutas con espacios:

```json
{
  "type": "command",
  "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/check.sh\""
}
```
Si se utiliza la forma desacoplada con argumentos (exec form), no se requiere cuoteo manual:

```json
{
  "type": "command",
  "command": "${CLAUDE_PLUGIN_ROOT}/scripts/check.sh",
  "args": ["--mode", "strict"]
}
```
