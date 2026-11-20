# Superficie de Acoplamiento de Synapse con Claude Code

> Inventario de todo lo que hoy ata Synapse al protocolo de Claude Code. Es la lista de puntos que un adaptador multi-agente debe cubrir. Basado en el código de `hooks/` a la versión `1.6.1` del plugin.

---

## 1. Diagrama del Contrato Actual

```text
          Claude Code (harness)                              Synapse (hooks/*.py)
+-----------------------------------+                +--------------------------------------+
| hooks/hooks.json                  |                | common.read_hook_input()             |
|  PreToolUse                       |   stdin JSON   |   tool_name, tool_input, cwd,        |
|   matcher "Bash"         ---------+--------------->|   session_id                         |
|   matcher "^(Edit|Write)$"        |                |                                      |
|   matcher "^Read$"                |                | lógica determinista + Laya           |
|                                   |                |                                      |
| env: CLAUDE_PLUGIN_ROOT           |                | common.emit_block()                  |
|      CLAUDE_PROJECT_DIR           |   exit 2 +     |   exit 2 + stderr "🚫 BLOCKED · ..."  |
|      CLAUDE_CONFIG_DIR            |<---------------+                                      |
|                                   |   exit 0 +     | common.emit_decision()               |
| UI: prompt de confirmación (ask)  |<---------------+   {"hookSpecificOutput": {...}}      |
+-----------------------------------+   stdout JSON  +--------------------------------------+
```

---

## 2. Puntos de Acoplamiento

### 2.1 Entrada (`stdin`)

| Campo | Lo consume | Uso |
| :--- | :--- | :--- |
| `tool_name` | Los 4 hooks, `common.start_trace` | Despacho: `Bash`, `Read`, `Edit`, `Write` |
| `tool_input.command` | `block_dangerous`, `block_env_read` | Comando de shell completo (string) |
| `tool_input.file_path` | `protect_files`, `block_env_read` (Read) | Ruta absoluta del archivo |
| `tool_input.content` | `detect_secrets` (Write) | Contenido completo a escribir |
| `tool_input.new_string` | `detect_secrets` (Edit) | Texto de reemplazo |
| `cwd` | `block_dangerous`, `protect_files`, `block_env_read`, `detect_secrets` | Resolución de rutas relativas |
| `session_id` | `common.start_trace` | Correlación en la traza JSONL (8 caracteres) |

**Supuestos implícitos que otros agentes rompen:**

1. **Un archivo por llamada de escritura.** `apply_patch` (Codex, OpenCode con modelos GPT, Copilot, Factory) toca varios archivos en un solo sobre.
2. **El contenido nuevo llega en claro.** En Codex y OpenCode llega como patch con líneas `+`; en Cline como diff SEARCH/REPLACE.
3. **Existe una herramienta de lectura.** Codex no tiene `Read`: todo pasa por shell o MCP.
4. **Los nombres de campo son snake_case Claude.** Antigravity usa camelCase + PascalCase (`toolCall.args.CommandLine`), OpenCode V2 usa `path`, Copilot usa `path`/`file_text`.

### 2.2 Salida

| Función | Protocolo | Dependencia de Claude |
| :--- | :--- | :--- |
| `emit_block` | `exit 2` + texto en `stderr` | Alta portabilidad: Codex, Gemini CLI, Cursor, Copilot, Kiro, Factory, Devin lo aceptan. **Antigravity no** (exit ≠ 0 se ignora, falla abierto). Cline tampoco (bloquea solo con `cancel`). |
| `emit_decision("ask")` | `hookSpecificOutput.permissionDecision: "ask"` | Baja portabilidad: Codex y Gemini CLI lo ignoran (falla abierto). Cursor solo lo soporta en `beforeShellExecution`. |
| `emit_decision("allow")` | `permissionDecision: "allow"` | Codex lo **rechaza** si no va con `updatedInput` (el hook queda como fallido). |
| `context=` | `hookSpecificOutput.additionalContext` | Solo Codex (≤2500 tokens) y Factory lo aceptan en PreToolUse. Gemini CLI, Antigravity y Kiro lo ignoran. |

### 2.3 Variables de Entorno

| Variable | Dónde | Equivalentes en otros hosts |
| :--- | :--- | :--- |
| `CLAUDE_PLUGIN_ROOT` | `hooks/hooks.json` (rutas a los scripts) | Codex: `PLUGIN_ROOT` + alias `CLAUDE_PLUGIN_ROOT`. Cursor: `CURSOR_PLUGIN_ROOT`. Gemini: `${extensionPath}`. Antigravity: cwd = raíz del plugin. Copilot: no documentado para hooks. |
| `CLAUDE_PROJECT_DIR` | `common.allowed_roots` (ancla del alcance) | Gemini: `GEMINI_PROJECT_DIR` + alias `CLAUDE_PROJECT_DIR`. Cursor: `CURSOR_PROJECT_DIR` + alias. Copilot: `CLAUDE_PROJECT_DIR` (con reportes de ausencia). Codex: **no existe**, usar `cwd`. Antigravity: `workspacePaths[0]`. OpenCode: `ctx.location.project.directory`. |
| `CLAUDE_CONFIG_DIR` | `common.get_log_dir`, `common.claude_memory_dir` | Codex: `CODEX_HOME`. Gemini/Antigravity: `~/.gemini`. Cursor: `~/.cursor`. Copilot: `COPILOT_HOME`. |

> [!WARNING]
> `allowed_roots()` usa `CLAUDE_PROJECT_DIR or cwd`. En un host que no define la variable, el ancla cae al `cwd` del payload. Si el agente hizo `cd` a un subdirectorio, el alcance sigue siendo correcto porque `_roots_for` sube al toplevel git; pero si el `cwd` está **fuera** del repo (por ejemplo, Antigravity, donde los hooks de plugin corren con cwd = raíz del plugin), el alcance queda anclado al plugin y no al proyecto. Ese caso debe resolverse con `workspacePaths[]`.

### 2.4 Manifiestos y Empaquetado

| Artefacto | Ruta actual | Problema al portar |
| :--- | :--- | :--- |
| Manifiesto | `.claude-plugin/plugin.json` | Cada host tiene el suyo; algunos leen el de Claude como compatibilidad (Codex, Copilot legado, VS Code, Devin, Factory). |
| Hooks | `hooks/hooks.json` | La ruta coincide con Codex, Gemini CLI, Cursor y Copilot legado, **pero el contenido no** (eventos y matchers distintos). Dos hosts no pueden compartir el mismo archivo con eventos distintos. |
| Skill | `skills/synapse-status/SKILL.md` | Formato portable (Codex, Antigravity, Gemini, OpenCode, Copilot, Cursor leen `SKILL.md`). |
| Regla | `rules/security-guardrails.md` | Ningún host la carga desde un plugin salvo Antigravity (con frontmatter `trigger`) y Cursor (`.mdc`). |

### 2.5 Archivos Protegidos Específicos de Claude

`protect_files.py` pide confirmación al editar `.claude/settings.json`, `.claude/hooks/**`, `.claude/synapse-commands.json` y `.claude/synapse-secrets.json`. Cada host tiene sus propios archivos de gobierno que merecen el mismo trato:

| Host | Archivos de configuración que un agente no debería reescribir sin confirmar |
| :--- | :--- |
| Codex | `.codex/config.toml`, `.codex/hooks.json`, `.codex/rules/*.rules`, `AGENTS.override.md` |
| Antigravity | `.agents/hooks.json`, `.agents/mcp_config.json`, `~/.gemini/antigravity-cli/settings.json` |
| Gemini CLI | `.gemini/settings.json`, `.gemini/policies/*.toml` |
| OpenCode | `opencode.json(c)`, `.opencode/plugins/**` |
| Cursor | `.cursor/hooks.json`, `.cursor/rules/**` |
| Copilot | `.github/hooks/*.json`, `.github/copilot/settings*.json` |

### 2.6 Rutas de Datos de Synapse

- Traza: `${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse/`.
- Memoria del proyecto como raíz permitida: `${CLAUDE_CONFIG_DIR}/projects/<ruta>/memory` (concepto exclusivo de Claude Code).
- Configuración por proyecto: `.claude/synapse-commands.json`, `.claude/synapse-secrets.json`.

Todas necesitan un nombre neutral o un fallback por host (ver [`09-propuesta-de-arquitectura.md`](./09-propuesta-de-arquitectura.md)).

---

## 3. Lo que NO está acoplado

El núcleo de decisión es independiente del host y se reutiliza tal cual:

- Tokenización y análisis estructural de shell (`extract_bash_tokens`, `parse_command_targets`, `expand_word`).
- Reglas de alcance git (`_roots_for`, worktrees, submódulos).
- Detección de secretos por firmas y entropía.
- Daemon de Laya (socket Unix, independiente del proceso que lo invoca).
- Traza JSONL y atribución `decided_by`/`rule`.

Esto confirma que el trabajo de portabilidad se concentra en **dos funciones de I/O** (`read_hook_input`, `emit_*`), en `allowed_roots`/`get_log_dir`, y en **manifiestos por host**.
