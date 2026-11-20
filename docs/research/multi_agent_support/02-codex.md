# OpenAI Codex

> Superficie completa de extensibilidad de OpenAI Codex (CLI `0.171.0`, app de escritorio de ChatGPT, extensión de IDE y ChatGPT/cloud): plugins y manifiestos, 14 eventos de hook, skills, prompts, subagentes, MCP, `AGENTS.md`, execpolicy, sandbox, permisos, statusline MCP, temas, notificaciones, marketplaces, configuración gestionada y compatibilidad con plugins de Claude Code. Verificado contra docs oficiales y el código de `openai/codex` (`main`). Corte al 2026-11-19.

---

## 1. Visión General y Superficies

| Superficie | Versión | Plugins | Hooks | Skills | MCP | Agentes propios |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Codex CLI** (`openai/codex`, Rust) | **0.171.0** ([releases](https://github.com/openai/codex/releases)) | ✅ `/plugins`, `codex plugin` | ✅ | ✅ | ✅ | ✅ |
| **App de escritorio ChatGPT** (macOS, Windows; `codex app`) | 26.1111.21308 (incluye CLI 0.169.0-alpha) | ✅ pestaña Plugins | ✅ Settings > Hooks | ✅ | ✅ | ✅ |
| **Extensión de IDE** (`openai.chatgpt`: VS Code, Cursor, Windsurf) | no verificada | ❌ ([plugins](https://learn.chatgpt.com/docs/plugins.md)) | ❓ | ✅ | ❓ | ✅ |
| **ChatGPT web/móvil y Codex cloud ("Work Cloud")** | — | ✅ skills y MCP de plugins | ❌ de plugins; solo hooks `mcp_tool` globales del admin (Agent Security) | ✅ | ✅ remoto | ❌ |

- `codex mcp-server` (Codex como servidor MCP) **fue eliminado**; lo reemplaza `codex app-server` (JSON-RPC propio, experimental) ([mcp-server](https://learn.chatgpt.com/docs/mcp-server.md)).
- Desde 0.140.0, **`/import`** migra la configuración de Claude Code y Cursor ([import](https://learn.chatgpt.com/docs/import.md)).

---

## 2. Modelo de Extensión

Un **plugin** empaqueta skills, servidores MCP (`mcp.json`/`.mcp.json`), "apps" (`.app.json`, que mapea servidores MCP registrados), hooks (solo locales) y assets de presentación. **No** aporta subagentes, comandos nativos, LSP, temas, output styles ni `bin/` en PATH. Se identifica como `plugin-name@marketplace-name`; `name` es también el namespace de sus skills.

### 2.1 Directorios

`CODEX_HOME` (por defecto `~/.codex`) contiene `config.toml`, `auth.json` (o keyring), `history.jsonl`, `hooks.json`, `AGENTS.md`, `agents/`, `themes/`, `prompts/` (obsoleto), `rules/`, `plugins/cache/` y `plugins/data/` ([config-advanced](https://learn.chatgpt.com/docs/config-file/config-advanced.md)).

### 2.2 Capas de `config.toml`

| Orden (menor → mayor) | Ubicación |
| :--- | :--- |
| 1 | `~/.codex/config.toml` |
| 2 | Perfil `~/.codex/<perfil>.config.toml` con `--profile` (desde 0.134.0 ya no `[profiles.x]`) |
| 3 | `.codex/config.toml` desde la raíz hasta el `cwd`, **solo en proyectos de confianza** (`projects.<ruta>.trust_level = "trusted"`); ignora `notify`, `profile(s)`, `model_provider(s)`, `otel`, `openai_base_url`… |
| 4 | Flags y `-c` |

**Gestionado** ([managed-configuration](https://learn.chatgpt.com/docs/enterprise/managed-configuration.md)): `/etc/codex/managed_config.toml` (defaults modificables; MDM `config_toml_base64`) y `requirements.toml` (restricciones; precedencia: `/etc/codex/requirements.toml` < Agent Security en la nube < campos legacy de `managed_config.toml` < MDM `requirements_toml_base64`).

**Scope de un plugin:** se instala por usuario (caché en `CODEX_HOME`) y se habilita en `config.toml` de usuario o proyecto con `[plugins."name@marketplace"] enabled = true`. No hay scope "local".

---

## 3. Manifiesto

### 3.1 Orden de Búsqueda

Verificado en `codex-rs/utils/plugins/src/plugin_namespace.rs`:

1. **`plugin.json` en la raíz, solo si su `$schema` es de agent-plugins.org** (Agent Plugins 1.0). Sin ese `$schema` se ignora.
2. `.codex-plugin/plugin.json`
3. **`.claude-plugin/plugin.json`**
4. `.cursor-plugin/plugin.json`

Los manifiestos que son symlinks se rechazan.

### 3.2 Formato Portable (Agent Plugins 1.0)

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "mi-plugin",
  "version": "1.0.0",
  "description": "...",
  "author": { "name": "..." },
  "extensions": {
    "com.openai": {
      "hooks": "./hooks/hooks.json",
      "apps": "./.app.json",
      "onboardingSkill": "setup",
      "interface": { "displayName": "Mi Plugin", "category": "Productivity" }
    }
  }
}
```

- Núcleo: `$schema` y `name` (1–64; minúsculas, dígitos, `-`, `.`; sin `--` ni `..`) requeridos; `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `extensions` opcionales. Schema cerrado: campos desconocidos se reportan y se ignoran ([spec](https://agent-plugins.org/specification)).
- Descubrimiento por convención: `skills/<n>/SKILL.md` (solo hijos directos) y `mcp.json` en la raíz. La spec v1 solo define skills y MCP.
- `extensions["com.openai"].hooks` (ruta, rutas, inline o lista) **reemplaza** el descubrimiento de `hooks/hooks.json`. Un `extensions.com.openai` objeto reemplaza por completo a `.codex-plugin/plugin.json`.

> [!WARNING]
> **Discrepancia código vs documentación:** en `main`, `core-plugins/src/loader.rs` devuelve una lista vacía de hooks cuando el formato es Agent Plugin, aunque los docs dicen que `extensions.com.openai.hooks` funciona. Para garantizar que los hooks carguen, usar el formato legacy (`.codex-plugin/` o `.claude-plugin/`).

### 3.3 Formato Legacy (`.codex-plugin/plugin.json`)

Campos (`codex-rs/core-plugins/src/manifest.rs`, `RawPluginManifest`, camelCase):

| Campo | Detalle |
| :--- | :--- |
| `name` | Vacío → nombre del directorio |
| `version`, `description`, `keywords[]` | |
| `skills` | Ruta o arreglo; por defecto `skills/` (búsqueda recursiva) |
| `mcpServers` | Ruta u objeto inline; por defecto `.mcp.json` |
| `apps` | Por defecto `.app.json` |
| `hooks` | Ruta, rutas, inline o lista; por defecto `hooks/hooks.json` |
| `commands` | Ruta o rutas; **solo para migrarlos a skills** (§6) |
| `interface` | `displayName`, `shortDescription`, `longDescription`, `developerName`, `category`, `capabilities[]`, `websiteURL`, `privacyPolicyURL`, `termsOfServiceURL`, `defaultPrompt` (≤3 × 512), `brandColor`, `composerIcon`, `logo`, `logoDark`, `screenshots[]` |

Rutas con `./`, relativas a la raíz y sin salir de ella. Sin comando de validación local: Codex registra warnings y omite lo inválido; la validación estricta está en el portal de envío ([submission-errors](https://developers.openai.com/plugins/deploy/submission-errors)).

### 3.4 `.claude-plugin/plugin.json`

Se lee con el mismo parser legacy. Se respetan `name`, `version`, `description`, `keywords`, `skills`, `mcpServers`, `hooks`, `commands`. Se ignoran en silencio `userConfig`, `agents`, `outputStyles`, `lspServers`, `monitors`, `settings`, `dependencies`, `channels` (y `author`, `homepage`, `repository`, `license`, que el struct legacy no tiene).

---

## 4. Hooks

Fuentes: [hooks](https://learn.chatgpt.com/docs/hooks.md), [schemas generados](https://github.com/openai/codex/tree/main/codex-rs/hooks/schema/generated).

### 4.1 Configuración

Mismo formato JSON que Claude (`{"hooks": {"<Evento>": [{"matcher": "regex", "hooks": [...]}]}}`) o inline `[hooks]` en TOML (warning si ambas en la misma capa).

| Ubicación | Nota |
| :--- | :--- |
| `/etc/codex/hooks.json` | Sistema |
| `$CODEX_HOME/hooks.json` | Usuario |
| `.codex/hooks.json` | Proyecto de confianza (raíz → `cwd`) |
| Plugin | `hooks/hooks.json` o campo `hooks` |
| `requirements.toml` `[hooks]` | Gestionados (`managed_dir` / `windows_managed_dir`); confiados por política, no deshabilitables |

| Handler | Campos |
| :--- | :--- |
| `command` | `command`, `commandWindows`, `timeout`, `statusMessage`, `additionalContextLimit`, `async` |
| `mcp_tool` | `server`, `tool`, `input` (plantillas `${field.nested}`), `timeout`, `statusMessage`. Usa una conexión existente; **los errores no bloquean** |
| `prompt`, `agent` | Se parsean pero **se omiten** |

- **Timeouts:** 600 s; `SessionEnd` e `Interrupt` 1 s (máx. 3 s).
- **Async** (`command` con `async: true`): hasta 8 concurrentes por sesión; salida "en el próximo punto seguro"; no bloquea.
- **Ejecución:** todos los hooks que coinciden, de todas las fuentes, corren **en paralelo** (un mismo comando en dos archivos corre dos veces), con `$SHELL -lc` en el `cwd` de la sesión.
- **Variables:** `PLUGIN_ROOT`, `PLUGIN_DATA`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`. **No hay `CLAUDE_PROJECT_DIR`** (usar `cwd` del payload).
- **Códigos:** 0 éxito; 2 + stderr bloquea (donde aplica); timeout o error → hook fallido y la operación sigue (**falla abierto**).
- **`additionalContext`:** tope ~2500 tokens; el exceso va a `<temp>/hook_outputs/<session>/<uuid>.txt`.
- **Confianza:** todo hook no gestionado se omite hasta revisarlo en `/hooks`; queda atado al hash (**cambiar los scripts obliga a revisar de nuevo**). `--dangerously-bypass-hook-trust` salta la revisión por una invocación. `allow_managed_hooks_only = true` deja solo los gestionados. Instalar desde la web **no despliega** scripts de hooks (usar MDM).
- Activos por defecto; `[features] hooks = false` los apaga.

### 4.2 Eventos

Campos comunes: `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `model`, y `permission_mode`/`turn_id` donde aplica.

| Evento | Momento | Matcher | Entrada extra | Salida / decisiones |
| :--- | :--- | :--- | :--- | :--- |
| `SessionStart` | Inicio de sesión o subagente | `source`: startup, resume, clear, compact, fork | `source` | stdout plano → contexto de developer; `additionalContext`; `continue:false` |
| `SessionEnd` | Cierre, archivado, borrado o 30 min inactivo | `reason` (hoy solo `other`) | `reason` | Informativo, síncrono, sin `mcp_tool` |
| `UserPromptSubmit` | Antes de enviar el prompt | — | `prompt` | stdout → contexto; `decision:"block"` + `reason` o exit 2 bloquean |
| `PreToolUse` | Antes de un tool | Nombre del tool (`Bash`; `apply_patch` con alias `Edit`/`Write`; `mcp__s__t`; `spawn_agent` con alias `Agent`) | `tool_name`, `tool_use_id`, `tool_input` | `permissionDecision:"deny"` + `permissionDecisionReason`; `allow` **solo con `updatedInput`**; `additionalContext`, `systemMessage`. **`ask` no soportado (falla abierto)** |
| `PermissionRequest` | Antes de un prompt de aprobación que Codex ya iba a mostrar | Nombre del tool | `tool_input.description` | `decision.behavior`: allow (salta el prompt) o deny (+ `message`); cualquier deny gana |
| `PostToolUse` | Después del tool (incluso Bash con exit ≠ 0) | Nombre del tool | `tool_response` | `decision:"block"` reemplaza el resultado con feedback (sin deshacer efectos); `additionalContext`; `continue:false` |
| `PreCompact` / `PostCompact` | Antes/después de compactar | `trigger`: manual, auto | `trigger` | JSON común |
| `Stop` | Fin del turno | — | `stop_hook_active`, `last_assistant_message` | **JSON obligatorio**; `decision:"block"` crea continuación con `reason` |
| `SubagentStart` | Inicio de subagente | `agent_type` | `agent_id`, `agent_type` | `additionalContext` va al subagente |
| `SubagentStop` | Fin de subagente | `agent_type` | `agent_transcript_path`, `last_assistant_message` | JSON obligatorio; `block` lo hace continuar |
| `PreModelRequest` / `PostModelRequest` | Antes/después de cada request al modelo | — | `status`, `error`, `token_usage` | Diagnóstico |
| `Interrupt` | El usuario interrumpe | — | — | Solo `systemMessage` |

### 4.3 Tools y `tool_input`

| Propósito | `tool_name` | `tool_input` |
| :--- | :--- | :--- |
| Shell (clásico y unified exec) | `Bash` | `{command: string}` (`workdir` no verificado; `write_stdin` no re-dispara) |
| Crear / editar / borrar archivos | `apply_patch` | `{command: "<texto del patch>"}` |
| Lectura | — | **No existe**: shell, MCP o `mcp_resource` (`view_image` para imágenes) |
| MCP | `mcp__<servidor>__<tool>` | Argumentos crudos |
| Subagente | `spawn_agent` (alias `Agent`) | |

**Sobre `apply_patch`** (`codex-rs/core/src/tools/handlers/apply_patch.rs`):

```text
*** Begin Patch
*** Add File: src/config.py
+API_URL = "https://example.test"
*** Update File: src/app.py
*** Move to: src/main.py
@@ def main():
-    old_line()
+    new_line()
*** Delete File: legacy.txt
*** End Patch
```

Un patch puede tocar varios archivos; rutas relativas al `cwd`; contenido nuevo = cuerpo de `Add File` + líneas `+`. No distingue Write de Edit. Un `deny` rechaza el patch completo.

### 4.4 Mapeo con Claude Code

| Claude Code | Codex | Nota |
| :--- | :--- | :--- |
| `SessionStart` | `SessionStart` | Agrega `fork` |
| `SessionEnd` | `SessionEnd` | Timeout 1–3 s |
| `UserPromptSubmit` | `UserPromptSubmit` | Equivalente |
| `PreToolUse` | `PreToolUse` | Sin `ask`; `allow` exige `updatedInput`; sin tool `Read` |
| `PermissionRequest` | `PermissionRequest` | Sin `updatedPermissions`/`updatedInput` |
| `PostToolUse` | `PostToolUse` | Sin `updatedMCPToolOutput`; también corre con exit ≠ 0 |
| `Stop` / `SubagentStop` | Iguales | Exigen JSON |
| `SubagentStart` | `SubagentStart` | |
| `PreCompact` | `PreCompact` (+ `PostCompact`) | |
| `Notification` | — | Más cercano: `notify`, `tui.notifications` |
| — | `PreModelRequest`, `PostModelRequest`, `Interrupt` | Solo Codex |

> [!NOTE]
> La documentación pide tratar los hooks como *"a useful guardrail, not a complete enforcement boundary"*: los tools hosteados (WebSearch) no pasan por hooks.

---

## 5. Skills

Fuente: [build-skills](https://learn.chatgpt.com/docs/build-skills.md).

| Scope | Ubicación |
| :--- | :--- |
| Repo | `.agents/skills` en cada directorio del `cwd` a la raíz del repo |
| Usuario | `~/.agents/skills` (**no** `~/.codex/skills`) |
| Admin | `/etc/codex/skills` |
| Sistema | Incluidas: `skill-creator`, `skill-installer`, `create-statusline`… |
| Plugin | `skills/` del plugin |

- Symlinks se siguen; dos skills homónimas aparecen ambas. Límites: `name` ≤ 64, profundidad 6, máx. 2000 directorios por raíz (`codex-rs/ext/skills/src/loader/mod.rs`).
- **Frontmatter** (`codex-rs/skills/src/parser.rs`): solo `name` (por defecto el directorio), `description` (obligatoria) y `metadata.short-description`. **Todo lo demás se ignora**: `allowed-tools`, `disable-model-invocation`, `model`, `argument-hint`, `user-invocable`, `context`…
- **`agents/openai.yaml`**: `interface` (`display_name`, `short_description`, `icon_small`, `icon_large`, `brand_color`, `default_prompt`), `policy.allow_implicit_invocation` (**equivale a `disable-model-invocation`** con `false`), `policy.products`, `dependencies.tools[]` (`type: mcp`, `value`, `transport`, `command`, `url`, `oauth_callback_port`). Con `features.skill_mcp_dependency_install` instala las dependencias MCP faltantes.
- Invocación: `$skill` o `/skills`; implícita por descripción. El catálogo inicial usa ≤ 2 % del contexto u 8000 caracteres (`skills.max_context_tokens`, hasta 10 000).
- Configuración: `[[skills.config]]` con `name`/`path` y `enabled = false`; `skills.bundled.enabled`, `skills.include_instructions`.
- Auxiliares: `scripts/`, `references/`, `assets/`.

---

## 6. Comandos Slash y Prompts

- **No hay comandos slash definidos por usuario ni por plugins**; los comandos son internos ([developer-commands](https://learn.chatgpt.com/docs/developer-commands.md?surface=cli)).
- **Custom prompts** `~/.codex/prompts/*.md` ([custom-prompts](https://learn.chatgpt.com/docs/custom-prompts.md)): **obsoletos** (usar skills); solo personales, nivel superior. Frontmatter `description`, `argument-hint`; placeholders `$1`–`$9`, `$ARGUMENTS`, `$NOMBRE` (`KEY=valor`), `$$`; invocación `/prompts:<nombre>`. Un plugin no los aporta.
- **Migración automática de `commands/` de un plugin a skills al instalar** (`codex-rs/core-plugins/src/command_migration.rs`): lee `commands/**/*.md` (o el campo `commands`), escribe en `<plugin>/.codex-plugin/migrated-command-skills/` con nombre `source-command-<ruta>` (≤ 64). **Se descartan** los comandos sin `description`, de más de 4000 bytes, llamados `README`, con `$ARGUMENTS`/`$1`, `{{…}}`, `` !`cmd` ``, `@archivo` o nombre duplicado. El resultado se invoca como skill (`$source-command-x`), no como `/comando`.

---

## 7. Subagentes

Fuente: [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents.md).

```toml
# ~/.codex/agents/reviewer.toml  o  .codex/agents/reviewer.toml
name = "reviewer"
description = "Revisa cambios antes de commitear"
developer_instructions = """..."""
model = "gpt-5.5"
model_reasoning_effort = "high"

[features]
shell_tool = false
```

- Requeridos: `name`, `description`, `developer_instructions`. Opcionales: `model`, `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity`, `features.*` (quitar `shell_tool`, `apps`, `plugins`, `memories`, `request_permissions_tool`), `skills.*`.
- No se pueden sobrescribir: `sandbox_mode`, `approval_policy`, `model_provider`, `mcp_servers` (se heredan). **Sin lista de tools** como en Claude.
- Integrados: `default`, `worker`, `explorer` (uno propio con el mismo nombre gana).
- Invocación: pidiéndolo en el prompt o por instrucciones; `/subagents`; tool `spawn_agent`.
- `[agents]`: `enabled`, `max_concurrent_threads_per_session`, `default_subagent_model`, `default_subagent_reasoning_effort`, `interrupt_message`, `agents.<n>.description`, `agents.<n>.config_file`.
- **Los plugins no pueden aportar agentes** (el portal pide convertirlos en skills).

---

## 8. MCP

Fuente: [extend/mcp](https://learn.chatgpt.com/docs/extend/mcp.md).

| Aspecto | Detalle |
| :--- | :--- |
| Config | `[mcp_servers.<n>]` en `config.toml` de usuario o proyecto de confianza |
| stdio | `command`, `args`, `env`, `cwd`, `env_vars` (nombres u objetos con `source` local/remote) |
| Streamable HTTP | `url`, `bearer_token_env_var`, `http_headers`, `env_http_headers`, `auth` (`oauth` o `chatgpt`), `http_headers_helper` |
| SSE | No documentado |
| Comunes | `startup_timeout_sec` (10), `tool_timeout_sec` (60), `enabled`, `required`, `startup_readiness`, `enabled_tools`, `disabled_tools`, `default_tools_approval_mode` (`auto`, `prompt`, `writes`, `approve`), `tools.<t>.approval_mode`, `output_token_limit`, `environment_id`, `scopes`, `oauth{...}` |
| OAuth | `codex mcp login <n> [--scopes] [--oauth-client-registration auto\|cimd\|dcr]`; valida `iss`. `codex mcp add\|list\|logout`, `/mcp` |
| Desde plugin | `.mcp.json` (legacy) o `mcp.json` (portable), con `{"mcpServers": {...}}` o el mapa directo. En portable `type` es obligatorio y la auth va en `extensions["com.openai"].auth` (`none`, `oauth` con `client.mode` provided/dcr/cimd, `mixed`, `api_key` con `headerScheme`); sin `clientSecret` en el manifiesto. stdio: `cwd` por defecto = raíz del plugin. El usuario controla `plugins.<p>.mcp_servers.<s>.*`. Expansión de `${CLAUDE_PLUGIN_ROOT}` en `command`/`args`: no verificada |
| Enterprise | `requirements.toml` `[mcp_servers.<id>.identity]` por `command` o `url` (exact, prefix, regex) |

---

## 9. Instrucciones, Reglas y Políticas

### 9.1 `AGENTS.md`

Fuente: [agents-md](https://learn.chatgpt.com/docs/agent-configuration/agents-md.md).

- Global en `CODEX_HOME`: `AGENTS.override.md` o `AGENTS.md` (primer archivo no vacío).
- Proyecto: desde la raíz git hasta el `cwd`, un archivo por directorio (`AGENTS.override.md` → `AGENTS.md` → `project_doc_fallback_filenames`); se concatenan con el más cercano al final. Tope `project_doc_max_bytes` (32 KiB).
- **No lee `CLAUDE.md`** salvo en `project_doc_fallback_filenames` (`/import` lo convierte).
- `model_instructions_file` reemplaza las instrucciones integradas; `developer_instructions` las amplía; `/init` genera un `AGENTS.md`.
- **Un plugin no puede aportar reglas.** Alternativas: una skill; un hook `SessionStart` con `additionalContext` o stdout plano; el campo `instructions` de un servidor MCP. El portal rechaza `CLAUDE.md` dentro del plugin.

### 9.2 execpolicy (`.rules`)

Archivos Starlark en `~/.codex/rules/*.rules` y `<repo>/.codex/rules/` (proyecto de confianza); gestionado en `requirements.toml` `[rules] prefix_rules` ([rules](https://learn.chatgpt.com/docs/agent-configuration/rules)).

```python
prefix_rule(
    pattern = ["git", "push", "--force"],
    decision = "prompt",          # allow | prompt | forbidden
    justification = "Force push requiere confirmación",
)
```

Gana la decisión más restrictiva. Cadenas `bash -lc` se dividen solo si son lineales (`&&`, `||`, `;`, `|`). Sin regex ni lógica de rutas; experimental; `codex execpolicy check`. **Es el único "ask" real para comandos**, limitado a prefijos estáticos. No cubre `apply_patch`.

### 9.3 Sandbox y Perfiles de Permisos

- **Sandbox** (`read-only`, `workspace-write`, `danger-full-access`): con `workspace-write`, `.git`, `.agents`, `.codex` y `.aws` quedan de solo lectura; red apagada; Seatbelt (macOS), bubblewrap + seccomp (Linux/WSL) ([agent-approvals-security](https://learn.chatgpt.com/docs/agent-approvals-security)).
- **Perfiles de permisos** (beta): `[permissions.<n>.filesystem]` con `read`/`write`/`deny` por ruta o glob (p. ej. `"**/*.env" = "deny"` bajo `":workspace_roots"`), aplicados por el SO. En Linux/WSL requiere `glob_scan_max_depth`; no cubre archivos creados después del arranque; incompatible con `sandbox_mode` ([permissions](https://learn.chatgpt.com/docs/permissions)).
- `approval_policy`: `on-request`, `never` u objeto granular. `shell_environment_policy` filtra variables con `KEY`/`SECRET`/`TOKEN`.

---

## 10. Configuración del Plugin y Datos

| Claude Code | Codex |
| :--- | :--- |
| `userConfig` | **No existe**: sin prompts de instalación ni `${user_config.*}`. Alternativas: inputs de la skill o del tool MCP, OAuth 2.1 del MCP remoto, variables de entorno o archivo de config documentado |
| Secretos | Flujo de conexión del MCP (OAuth, `api_key`), nunca en el manifiesto; locales: `bearer_token_env_var`, `env_vars`, `env_http_headers` |
| `${CLAUDE_PLUGIN_DATA}` | `PLUGIN_DATA` (y alias) → `$CODEX_HOME/plugins/data/<plugin>-<marketplace>` (`core-plugins/src/store.rs`); para MCP de plugins portables `plugins/data/agent-plugins/<sha256>` |
| `${CLAUDE_PLUGIN_ROOT}` | `PLUGIN_ROOT` (y alias) → caché versionada |
| `bin/` en PATH | Sin evidencia; el portal dice "call them with package-relative paths" |
| Settings por defecto | No existen; el portal rechaza `settings.json` |

---

## 11. Mods de Interfaz

Todo vive en `config.toml` del usuario, **salvo la statusline vía MCP, que un plugin sí puede aportar** ([config-reference](https://learn.chatgpt.com/docs/config-file/config-reference.md), [cli-customization](https://learn.chatgpt.com/docs/cli-customization.md)).

| Mod | Detalle |
| :--- | :--- |
| Temas | `/theme` → `tui.theme`; temas propios `.tmTheme` en `$CODEX_HOME/themes` (solo resaltado de código y diffs) |
| Statusline integrada | `/statusline` → `tui.status_line` (`null` la desactiva) |
| **Statusline vía MCP** (0.171.0, experimental, flag `tui_extensions_statusline`) | Un servidor MCP stdio (global o **de un plugin**) anuncia un tool con `_meta["openai/ui"].entrypoints = [{"type": "statusline", "resourceUri": "ui://x/footer"}]`. Codex llama a `resources/read` con el estado en `params._meta["com.openai/codex-statusline-state"]`; responde un árbol UI JSON (`row`, `text`, `link`, `bold`, colores de tema). Límites: 64 KiB, 256 nodos, profundidad 16, 1 s por lectura, polling cada 1 s. Referencia: skill incluida `create-statusline` (`references/statusline.d.ts`) |
| Título | `/title`, `tui.terminal_title` |
| Output styles | No existen (`personality` obsoleta). Más cercano: `model_instructions_file`, `developer_instructions`, `model_verbosity` |
| Notificaciones | `notify = ["cmd", ...]` (JSON con `type`, `thread-id`, `turn-id`, `cwd`, `input-messages`, `last-assistant-message`; solo `agent-turn-complete`); `tui.notifications` (`agent-turn-complete`, `approval-requested`), `tui.notification_method` (`auto`, `osc9`, `bel`), `tui.notification_condition` |
| Atajos | `/keymap` → `tui.keymap.<contexto>.<acción>`; `/vim`, `tui.vim_mode_default` |
| Otros | `tui.animations`, `alternate_screen`, `show_tooltips`, `collapse_turn_details`, `raw_output_mode`, `auto_recap`, `tui.presets.<0-9>` (`/presets`), `/pets`, `Ctrl+G` editor externo |

---

## 12. LSP y Segundo Plano

- **LSP:** sin soporte nativo (ninguna llave `lsp`, sin módulos LSP en `codex-rs`). Propuesta abierta en el issue #8745. Alternativa: un puente MCP (p. ej. mcp-language-server). El portal marca `lspServers` como no soportado.
- **Monitores:** no existen. Sustitutos: terminales en background del unified exec (`/ps`, `/stop`, `background_terminal_max_timeout`), hooks `async`, statusline MCP con polling, `/goal`, `codex queue`.

---

## 13. Distribución

### 13.1 Marketplaces

`codex-rs/core-plugins/src/marketplace.rs`:

- Ubicaciones: `.agents/plugins/marketplace.json`, `.agents/plugins/api_marketplace.json`, **`.claude-plugin/marketplace.json`**, `.cursor-plugin/marketplace.json`; personal en `~/.agents/plugins/marketplace.json`.
- Campos: `name`, `interface.displayName`, `plugins[]` con `name`, `category`, `policy.installation` (`NOT_AVAILABLE`, `AVAILABLE`, `INSTALLED_BY_DEFAULT`), `policy.authentication` (`ON_INSTALL`, `ON_USE`).
- `source`: ruta `./…`, `{source: "local", path}`, `{source: "url", url, path?, ref?, sha?}`, `{source: "git-subdir", url, path, ref?|sha?}`, `{source: "npm", package, version?, registry?}`. **`{"source": "github", "repo": …}` de Claude no está en el enum**: la entrada se omite (inferido del código).

### 13.2 CLI e Instalación

- `codex plugin marketplace add owner/repo[@ref] | URL git | dir [--ref] [--sparse]`, `list`, `upgrade`, `remove`.
- `codex plugin add PLUGIN@MARKETPLACE [--json]`, `codex plugin list [--available]`, `codex plugin remove`; TUI `/plugins`.
- `marketplaces.<n>.source_type` (`git`, `local`), `source`, `ref`, `sparse_paths`; 0.171.0 agrega autoactualización (llave no verificada).
- Caché `~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/` (`local` para locales; `1.0.0` por defecto si el manifiesto portable no trae `version`). Reiniciar la sesión tras cambios.

### 13.3 Directorio Público

- Portal de OpenAI Platform (acceso a Apps Management y verificación de identidad). Universal para ChatGPT y Codex.
- **No acepta plugins con hooks** ni declaraciones `api_key`. MCP público solo HTTPS remoto con verificación de dominio (`/.well-known/openai-apps-challenge`).
- Acepta ZIP con `.codex-plugin`, `.agent-plugin` o **`.claude-plugin`** (convierte este último) ([submit-claude-plugin](https://developers.openai.com/plugins/guides/submit-claude-plugin)).
- Plugins de workspace quedan en el workspace; `features.plugin_sharing = false` bloquea publicar.

### 13.4 Enterprise

`requirements.toml`: `features.plugins = false`, `features.hooks`, `[marketplaces] restrict_to_allowed_sources = true` + `allowed_sources.<n>` (`git`, `host_pattern`, `local`), `plugins.<p>.mcp_servers.<s>.identity`, `allow_managed_hooks_only`, `[hooks]`, `[rules]`, `[permissions.filesystem] deny_read`.

---

## 14. Compatibilidad Directa con Plugins de Claude Code

| Se lee tal cual | Se convierte | Se ignora | Cambia de semántica |
| :--- | :--- | :--- | :--- |
| `.claude-plugin/plugin.json` | `commands/*.md` → skills (sin argumentos, `!`, `@`; con `description`; ≤ 4000 bytes) | `agents/`, `userConfig`, `outputStyles`, `lspServers`, `monitors`, `settings.json` | `permissionDecision:"ask"` → hook fallido, tool corre |
| `.claude-plugin/marketplace.json` (fuentes ruta, url, git-subdir, npm) | | `bin/` en PATH | `allow` sin `updatedInput` → hook fallido |
| `skills/*/SKILL.md` (solo `name`, `description`) | | `CLAUDE.md`, `rules/` | Sin tool `Read`: `^Read$` nunca coincide |
| `hooks/hooks.json` (misma forma; `CLAUDE_PLUGIN_ROOT`/`_DATA` definidos) | | `allowed-tools`, `model`, `disable-model-invocation` en skills | Edit/Write llegan como `apply_patch` |
| `.mcp.json` con `mcpServers` | | Fuente `github` en marketplace | Sin `CLAUDE_PROJECT_DIR` |
| | | | `Stop`/`SubagentStop` exigen JSON |
| | | | Handlers `prompt`/`agent` se omiten |
| | | | Cada cambio de script exige reconfiar en `/hooks` |

**`/import`** migra la configuración de Claude a nivel usuario o proyecto (instrucciones, `settings.json`, skills, comandos, subagentes, hooks, MCP, plugins, memorias); plugins, marketplaces y hooks pueden requerir ajuste manual.

---

## 15. Tabla de Compatibilidad por Componente

| Claude Code | Codex | ¿Soportado? | Notas |
| :--- | :--- | :---: | :--- |
| `.claude-plugin/plugin.json` | Manifiesto legacy o `plugin.json` portable | Sí | Ignora campos desconocidos |
| `userConfig` (+ secretos) | MCP OAuth/api_key, variables de entorno | No | |
| `${CLAUDE_PLUGIN_ROOT}` / `_DATA` | `PLUGIN_ROOT` / `PLUGIN_DATA` + alias | Sí (hooks) | En `.mcp.json`: no verificado |
| Skills | `skills/` | Parcial | Solo `name`, `description`, `metadata.short-description` |
| `disable-model-invocation` | `agents/openai.yaml` `policy.allow_implicit_invocation: false` | Parcial | Otro archivo |
| Comandos | Skill `source-command-*` | Parcial | Restricciones fuertes |
| Subagentes | `.codex/agents/*.toml` | No (desde plugin) | TOML, sin lista de tools |
| Hooks | `hooks/hooks.json` | Parcial | Sin `ask`; confianza por hash; no en directorio público ni nube |
| Hook `prompt` / `agent` | — | No | Se omiten |
| Hook `mcp_tool` | Igual | Sí | No bloquea si falla |
| `Notification` | `notify`, `tui.notifications` | Parcial | Config de usuario |
| MCP | `.mcp.json` / `mcp.json` | Sí | Portable exige `type` |
| LSP | — | No | Puente MCP |
| Monitores | — | No | Hooks async, terminales en background |
| `bin/` en PATH | Rutas relativas | No | |
| Settings por defecto | — | No | |
| Output styles | `model_instructions_file` (usuario) | No | |
| Temas | `.tmTheme` | No (desde plugin) | |
| Statusline | Statusline MCP (experimental) | Sí (desde plugin, vía MCP) | |
| Reglas / `CLAUDE.md` | `AGENTS.md`, hook `SessionStart`, skill | No (desde plugin) | |
| Marketplace | `.agents/plugins/` o `.claude-plugin/marketplace.json` | Parcial | Sin fuente `github` |
| Instalación | `codex plugin add/remove`, `/plugins` | Sí | |
| Scopes | Usuario / proyecto de confianza / gestionado | Parcial | Sin "local" |
| Validación | Portal de envío | — | Sin comando local |

---

## 16. No Verificado

1. Carga de hooks en el formato portable (`loader.rs` devuelve vacío; los docs dicen que sí).
2. Expansión de `${CLAUDE_PLUGIN_ROOT}`/`${PLUGIN_ROOT}` en `command`/`args` del `.mcp.json` de un plugin.
3. `bin/` en el PATH (probablemente no).
4. Posición de `/etc/codex/config.toml` en la precedencia.
5. Llave de autoactualización de marketplaces y política SemVer.
6. Versión de la extensión de IDE y si corre hooks, MCP y `config.toml`.
7. SSE en MCP; `codex mcp get/remove`.
8. Si un plugin puede aportar temas, `.rules` o `AGENTS.md`.
9. Omisión de la fuente `github` (inferida, no probada).
10. Comportamiento real de `/import` con plugins de Claude.
11. Pendientes heredados: `workdir` en `tool_input` de `Bash`, si unified exec dispara siempre `PreToolUse`, `exit 2` en Linux con la versión actual, `additionalContext` sin `permissionDecision`.

**Fuentes secundarias** (solo como señal): [classmethod](https://dev.classmethod.jp/en/articles/codex-cli-import-from-claude-code/), [codex LSP](https://codex.danielvaughan.com/2026/04/25/codex-cli-lsp-integration-language-server-semantic-code-intelligence/), [AWS cross-tool comparison](https://awslabs.github.io/aidlc-workflows/reference/research/Cross-Tool%20Plugin%20Comparison/), [destructive_command_guard](https://raw.githubusercontent.com/Dicklesworthstone/destructive_command_guard/main/docs/codex-integration.md).
