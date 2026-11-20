# Cursor (IDE y `cursor-agent`)

> Hooks de agente, importación de hooks de Claude y plugins de Cursor evaluados contra los 4 hooks de Synapse. Cursor tiene eventos dedicados por tipo de acción, acepta `exit 2` y define `CLAUDE_PROJECT_DIR` como alias. El riesgo principal es el **import automático de plugins de Claude, que hoy falla abierto**.

---

## 1. Hooks de Agente

Fuente: [docs/agent/hooks](https://cursor.com/docs/agent/hooks).

### 1.1 Eventos Relevantes

| Evento | Momento | stdin propio | Decisiones |
| :--- | :--- | :--- | :--- |
| `beforeShellExecution` | Antes de un comando de shell | `command`, `cwd`, `sandbox` | `allow` / `deny` / **`ask`** (funciona) |
| `beforeReadFile` | Antes de leer | `file_path`, `content`, `attachments[]` | `allow` / `deny` (sin `ask`) |
| `preToolUse` | Antes de cualquier tool (matcher por tipo) | `tool_name`, `tool_input` | `allow` / `deny`; `ask` **"no se aplica todavía"** (confirmado por Cursor) |
| `afterFileEdit` | **Después** de escribir | `edits[{old_string, new_string}]` | Informativo |
| `beforeMCPExecution` | Antes de un tool MCP | | |

> [!WARNING]
> **No existe `beforeFileEdit`.** Para inspeccionar escrituras antes de que ocurran hay que usar `preToolUse` con matcher `Write`. La documentación no detalla `tool_input` para `Write`; un [reporte del foro](https://forum.cursor.com/t/pretooluse-hook-updated-input-silently-discarded-for-write-tool-file-creation-edit/165962) reescribe `tool_input.content` en `preToolUse`, lo que sugiere que el contenido llega, y el mismo reporte dice que `updated_input.content` se descarta. **Hay que verificarlo registrando el stdin real.**

### 1.2 Matcher

Regex sobre el tipo de herramienta: `Shell`, `Read`, `Write`, `Grep`, `Delete`, `Task`, `MCP:<tool>`. En los hooks de shell, el matcher se evalúa contra el **comando completo**.

### 1.3 Configuración

| Nivel | Ruta |
| :--- | :--- |
| Proyecto | `.cursor/hooks.json` |
| Usuario | `~/.cursor/hooks.json` |
| Enterprise (MDM) | `/etc/cursor/hooks.json` (Linux), `/Library/Application Support/Cursor/hooks.json` (macOS), `C:\ProgramData\Cursor\hooks.json` (Windows) |
| Team | Dashboard |

```json
{
  "version": 1,
  "hooks": {
    "beforeShellExecution": [
      { "command": "python3 ./hooks/block_dangerous.py", "timeout": 30, "failClosed": true }
    ]
  }
}
```

Campos: `command`, `matcher`, `timeout`, `failClosed`, `loop_limit`. Al combinar hooks: **deny > ask > allow**.

### 1.4 Entrada Común

`conversation_id`, `generation_id`, `hook_event_name`, `workspace_roots`, `user_email`, `transcript_path`, `cursor_version`.

> [!NOTE]
> `user_email` llega en cada payload. Hoy `common.start_trace` solo registra `cwd`, `command`, `file_path` y longitudes de contenido, así que no se filtra. El adaptador de Cursor debe mantener esa lista blanca y no volcar el payload completo a la traza.

### 1.5 Salida y Códigos de Salida

| Campo | Uso |
| :--- | :--- |
| `permission` | `allow` / `deny` / `ask` |
| `user_message` | Mensaje para el usuario |
| `agent_message` | Mensaje para el agente (equivale a `additionalContext`) |
| `updated_input` | Reescritura (solo `preToolUse`) |

| Código | Efecto |
| :--- | :--- |
| 0 | Se usa el JSON |
| **2** | **deny**, "como en Claude Code" |
| Otro | Deja pasar, salvo `failClosed: true` |
| JSON inválido en hook de permisos | Bloquea |

### 1.6 Variables de Entorno

`CURSOR_PROJECT_DIR`, **`CLAUDE_PROJECT_DIR` (alias)**, `CURSOR_PLUGIN_ROOT` (solo en hooks de plugin), `CURSOR_VERSION`, `CURSOR_TRANSCRIPT_PATH`.

---

## 2. Importación de Hooks y Plugins de Claude

Fuente: [third-party-hooks](https://cursor.com/docs/reference/third-party-hooks).

Con el setting **"Include Third-Party Plugins, Skills, and Other Configs"** (activo por defecto), Cursor lee `.claude/settings.local.json`, `.claude/settings.json` y `~/.claude/settings.json`, y mapea:

| Claude | Cursor |
| :--- | :--- |
| `PreToolUse` | `preToolUse` |
| `Bash` | `Shell` |
| `Edit` | `Write` |
| `Read`, `Write` | Igual |
| `permissionDecision` | `permission` |
| `permissionDecisionReason` | `user_message` |

> [!CAUTION]
> **Bug abierto (septiembre 2026):** `cursor-agent` importa `~/.claude/plugins/installed_plugins.json`, pero el payload que entrega a los hooks trae `tool_name: "Shell"` (no `Bash`) y `cwd: ""`, y `failClosed` siempre queda en `false`. **Un gate basado en `tool_name == "Bash"` deja pasar todo.** Synapse instalado como plugin de Claude en una máquina con Cursor queda en ese caso: `block_dangerous.main` retorna temprano porque `data.get("tool_name") != "Bash"`. Fuente: [foro](https://forum.cursor.com/t/cursor-agent-imported-claude-code-plugin-hooks-get-a-cursor-shaped-payload-so-pretooluse-gates-fail-open/173171).

Mitigaciones inmediatas, independientes del adaptador:

1. Aceptar `Shell` como alias de `Bash` en el despacho de `block_dangerous` y `block_env_read`.
2. Si `cwd` llega vacío, usar `CLAUDE_PROJECT_DIR` / `CURSOR_PROJECT_DIR` / `workspace_roots[0]`.

---

## 3. Plugins y Marketplace

Fuente: [plugins/building](https://cursor.com/docs/plugins/building) (Cursor 2.5, febrero 2026).

```text
synapse/
├── .cursor-plugin/
│   ├── plugin.json
│   └── marketplace.json   <-- multi-plugin
├── hooks/hooks.json       <-- eventos Cursor, no Claude
├── rules/*.mdc
├── skills/<name>/SKILL.md
├── agents/, commands/
└── mcp
```

- En la configuración MCP se expanden `${CURSOR_PLUGIN_ROOT}` y `${CLAUDE_PLUGIN_ROOT}`.
- **Las reglas `.mdc` sí se cargan desde un plugin**: `rules/security-guardrails.md` se puede publicar como `.mdc`.
- Según un [reporte del foro](https://forum.cursor.com/t/cursor-cli-ignores-hooks-from-marketplace-plugin/163890), el CLI ignora los hooks de plugins del marketplace.

---

## 4. Brechas por Hook

| Hook | Evento Cursor | Trabajo |
| :--- | :--- | :--- |
| `block_dangerous` | `beforeShellExecution` | Leer `command` + `cwd`. Salida `{"permission": ..., "user_message", "agent_message"}`. `ask` funciona. `failClosed: true`. |
| `block_env_read` | `beforeReadFile` + `beforeShellExecution` | `file_path`. `ask` no disponible en lectura: el bloqueo ya es `deny`, sin pérdida. |
| `detect_secrets` | `preToolUse` matcher `Write` | Campo de contenido por verificar. `ask` de Laya → `deny` con motivo, o `agent_message` de advertencia. |
| `protect_files` | `preToolUse` matcher `Write` (y `Delete`) | `ask` (settings de agentes) → `deny` con motivo. Agregar `.cursor/hooks.json`, `.cursor/rules/**`. |

---

## 5. Tabla de Compatibilidad

| Funcionalidad | ¿Soportada? | Notas | Fuente |
| :--- | :--- | :--- | :--- |
| Hook previo a shell | Sí | `beforeShellExecution`, con `ask` | hooks |
| Hook previo a lectura | Sí | `beforeReadFile`, sin `ask` | hooks |
| Hook previo a escritura | Parcial | `preToolUse` + `Write`, sin `ask` | hooks; foro |
| Contenido antes de escribir | Probable | No documentado | foro |
| `exit 2` = deny | Sí | | hooks |
| Fail-closed | Opcional | `failClosed: true` | hooks |
| Contexto al agente | Sí | `agent_message` | hooks |
| `CLAUDE_PROJECT_DIR` | Sí (alias) | | hooks |
| Import de hooks Claude | Sí, con bug | Falla abierto | third-party-hooks; foro |
| Plugins con hooks, reglas, skills | Sí | `.cursor-plugin/` | plugins |
| Hooks de plugins en CLI | Dudoso | Reporte de que se ignoran | foro |

---

## 6. Pendiente de Verificar

- `tool_input` real de `Write` (y si existe un tool de edición distinto) en `preToolUse`.
- Si `cursor-agent` ejecuta hooks de plugins del marketplace.
- Estado del bug de import (`tool_name: "Shell"`, `cwd: ""`).
