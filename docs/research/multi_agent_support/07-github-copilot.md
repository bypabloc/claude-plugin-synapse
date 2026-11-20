# GitHub Copilot (CLI, VS Code y Coding Agent)

> Hooks de Copilot CLI, del agente de VS Code y del coding agent en la nube, evaluados contra los 4 hooks de Synapse. Copilot es el host con **menor costo de port**: lee `.claude/settings.json`, acepta `.claude-plugin/plugin.json`, entrega payloads con nombres de tool Claude y respeta `exit 2`. La diferencia principal es que la **salida es plana** (sin `hookSpecificOutput`).

---

## 1. Copilot CLI

Fuentes: [hooks-configuration](https://docs.github.com/en/copilot/reference/hooks-configuration), [cli-hooks-reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference).

### 1.1 Ubicaciones

| Nivel | Ruta |
| :--- | :--- |
| Repo | `.github/hooks/*.json` |
| Usuario | `~/.copilot/hooks/` (o `$COPILOT_HOME/hooks`) |
| Inline | clave `hooks` en `.github/copilot/settings(.local).json` y `~/.copilot/settings.json` |
| **Compatibilidad Claude** | **`.claude/settings.json` y `.claude/settings.local.json` del repo** |
| Admin | `/etc/github-copilot/policy.d/*.json` |
| Plugin | `hooks.json` del plugin |

Formato con `"version": 1`. Cada entrada acepta `bash` / `powershell` / `command`, más `cwd`, `env`, `timeoutSec` (30 por defecto) o la forma sin shell `exec` + `args`. También hay hooks HTTP.

### 1.2 Eventos

`preToolUse`, `permissionRequest` (solo CLI; salida `behavior`/`message`/`interrupt`), `postToolUse`, `postToolUseFailure`, `sessionStart`, entre otros.

### 1.3 Dos Formas de Payload

| Nombre del evento | Payload |
| :--- | :--- |
| **`PreToolUse` (PascalCase)** | `hook_event_name`, `session_id`, `timestamp` (ISO), `cwd`, `tool_name`, `tool_input`, con **nombres de tool Claude**: `bash`/`powershell` → `Bash`, `view` → `Read`, `create` → `Write`, `edit`/`str_replace_editor`/`apply_patch` → `Edit` |
| `preToolUse` (camelCase) | `toolName` y `toolArgs` (que puede llegar como string JSON) |

Los matchers son regex anclados como `^(?:PATTERN)$`: **`Bash`, `^(Edit|Write)$` y `^Read$` funcionan tal cual** con la forma PascalCase.

> [!WARNING]
> **Solo se remapea el nombre del tool, no los argumentos.** Los nombres nativos parecen ser `path`, `file_text`, `old_str`/`new_str` (según los ejemplos del [SDK](https://github.com/github/copilot-sdk/blob/main/docs/hooks/pre-tool-use.md) y el schema antiguo de `str_replace_editor`). No está confirmado: hay que registrar un evento real por tool. Si se confirma, `detect_secrets` y `protect_files` leen `file_path`/`content`/`new_string` vacíos y **dejan pasar todo**.

Además, `apply_patch` se mapea a `Edit` pero su input es un patch: el mismo parser de Codex aplica.

### 1.4 Salida

```json
{ "permissionDecision": "deny", "permissionDecisionReason": "...", "modifiedArgs": { } }
```

- **Plana**, sin `hookSpecificOutput`. La salida anidada actual de Synapse probablemente se ignora (no verificado).
- La referencia no documenta `additionalContext` en `preToolUse` (un tutorial lo muestra; tratar como no verificado).

### 1.5 Códigos de Salida

| Situación | Efecto |
| :--- | :--- |
| `exit 2` | **deny**, aunque stdout diga allow |
| Otro código ≠ 0 | deny (**falla cerrado**) |
| **Timeout** | **Falla abierto** |

> [!CAUTION]
> El timeout por defecto es **30 s**. Un arranque en frío del daemon de Laya (~5 s) entra holgado, pero una GPU ocupada o un modo CPU lento puede acercarse al límite y dejar pasar el tool. Fijar `timeoutSec` explícito.

### 1.6 `ask`

Funciona en el CLI interactivo. **En modo autopilot y en el coding agent de la nube, `ask` se trata como `deny`.**

### 1.7 Variables de Entorno

- Hooks de plugin: `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_DATA` (según [tutorial](https://awesome-copilot.github.com/learning-hub/automating-with-hooks/)), `COPILOT_PLUGIN_DATA`, plantillas `{{project_dir}}` / `{{plugin_data_dir}}`.
- **`CLAUDE_PLUGIN_ROOT` no está documentado para comandos de hooks del CLI** (sí para MCP y agentes).
- Un [issue](https://github.com/github/copilot-cli/issues/4001) reporta `$CLAUDE_PROJECT_DIR` ausente en algunos casos.
- `CLAUDE_CONFIG_DIR` no se define.

### 1.8 Plugins y Marketplace

Fuente: [cli-plugin-reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference).

| Formato | Manifiesto | Hooks | Reglas |
| :--- | :--- | :--- | :--- |
| **Agent Plugins 1.0** | `plugin.json` en la raíz con `$schema` `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json` | `com.github.copilot/hooks/hooks.json` | `com.github.copilot/rules/` |
| Legado | Busca en `.plugin/`, la raíz, `.github/plugin/` y **`.claude-plugin/`** | `hooks.json` o `hooks/hooks.json` | |

Instalación: `copilot plugin install OWNER/REPO[:path]`, `copilot plugin marketplace add ...`. También se lee `marketplace.json` desde `.claude-plugin/`.

---

## 2. VS Code

| Harness | Estado | Comportamiento |
| :--- | :--- | :--- |
| **Copilot** | Preview | Mismo schema que el CLI (salida plana; `{}` = sin decisión; `ask` requiere aprobación interactiva). Lee `.github/hooks`, `~/.copilot/hooks`, `.claude/settings(.local).json` del repo (no `~/.claude/settings.json`), agent plugins y frontmatter de agentes. [Fuente](https://code.visualstudio.com/docs/copilot/customization/hooks) |
| "Local" | **Obsoleto, se retira en febrero 2027** | **Salida compatible con Claude** (`hookSpecificOutput.permissionDecision`, `updatedInput`, `additionalContext`), `exit 2` bloquea, input con `tool_name`, `tool_input`, `tool_use_id`. **Ignora matchers.** Nombres de tool nativos sin documentar. Requiere `chat.useHooks` y `chat.useClaudeHooks` (false por defecto). [Fuente](https://code.visualstudio.com/docs/agents/reference/hooks-reference) |
| Agent plugins | Sí | **Acepta `.claude-plugin/plugin.json` y define `CLAUDE_PLUGIN_ROOT`** en el proceso del hook (formato Copilot: `PLUGIN_ROOT`). Lee `skills/`. Marketplaces en `chat.plugins.marketplaces`. [Fuente](https://code.visualstudio.com/docs/agents/reference/agent-plugin-reference) |

---

## 3. Coding Agent en la Nube

- Lee **solo** `.github/hooks/*.json` del repo clonado, solo los campos `bash`/`command`.
- Sandbox Linux no interactivo con firewall.
- Plugins y hooks de `~/.copilot` no aplican.
- `ask` = `deny`.

Para cubrirlo, Synapse tendría que copiarse al repo (`.github/hooks/synapse.json` + scripts versionados), lo que choca con la idea de plugin instalable. Es un caso de distribución distinto.

---

## 4. Brechas por Hook

| Hook | Trabajo |
| :--- | :--- |
| `block_dangerous` | Usar evento PascalCase `PreToolUse`. Salida plana. `exit 2` funciona. `ask` funciona en CLI interactivo; se vuelve `deny` en autopilot/nube. |
| `block_env_read` | `Read` (desde `view`): mapear el argumento nativo de ruta a `file_path`. |
| `detect_secrets` | Mapear `file_text` → `content`, `new_str` → `new_string` (por confirmar). Parsear `apply_patch`. |
| `protect_files` | Mapear `path` → `file_path`. Agregar `.github/hooks/*.json`, `.github/copilot/settings*.json`. |
| Todos | No depender de `CLAUDE_PLUGIN_ROOT` / `CLAUDE_CONFIG_DIR`: resolver desde `__file__`. Fijar `timeoutSec`. |

---

## 5. Tabla de Compatibilidad

| Funcionalidad | ¿Soportada? | Notas |
| :--- | :--- | :--- |
| Hook previo | Sí | CLI, VS Code, nube |
| Lee `.claude/settings.json` | Sí | Repo, no usuario |
| Manifiesto `.claude-plugin` | Sí (legado) | CLI y VS Code |
| Nombres de tool Claude | Sí (PascalCase) | Argumentos no remapeados |
| Salida `hookSpecificOutput` | Probablemente no | Plana (salvo VS Code "Local") |
| `exit 2` = deny | Sí | Otros códigos también |
| `ask` | Parcial | `deny` en autopilot y nube |
| `additionalContext` | No documentado | |
| `CLAUDE_PROJECT_DIR` | Sí, con reportes de ausencia | |
| `CLAUDE_PLUGIN_ROOT` | VS Code sí; CLI no documentado | |
| Timeout | Falla abierto | 30 s por defecto |

---

## 6. Pendiente de Verificar

- Argumentos nativos reales de `view`, `create`, `edit`, `str_replace_editor`.
- Si Copilot ignora `hookSpecificOutput` o lo interpreta.
- `CLAUDE_PLUGIN_ROOT` en hooks del CLI.
- Nombres de tool del harness "Local" de VS Code.
