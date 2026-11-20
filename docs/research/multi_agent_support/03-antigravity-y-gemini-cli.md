# Google Antigravity y Gemini CLI

> Superficie completa de extensibilidad de Google Antigravity (App 2.33, CLI `agy` 1.5.12, IDE standalone 2.8, extensiones de IDE) y de Gemini CLI (v0.70, destino heredado): plugins y extensiones, hooks, skills, comandos, subagentes, MCP, reglas, variables, sidecars, statusline, temas, paneles de UI y distribución. Corte al 2026-11-19.

---

## 1. Contexto y Versiones

```text
 2025-11      Antigravity (IDE agéntico, fork de VS Code) se lanza
 2026-01-28   Gemini CLI v0.26.0: hooks activos por defecto
 2026-05-19   Gemini CLI se integra en Antigravity:
              Antigravity CLI (`agy`, Go) + Antigravity 2.0 (desktop) comparten harness
 2026-06-18   Gemini CLI deja de servir a usuarios gratuitos y Google AI Pro/Ultra
              (sigue con API key de pago y licencias Code Assist Standard/Enterprise)
 2026-11      App 2.33.0 · CLI 1.5.12 · IDE 2.8.0 · ext. VS Code 1.13.0 · SDK Python 0.1.24
              Gemini CLI v0.70.0 estable (repo activo, Apache-2.0)
```

Fuentes: [transición](https://developers.googleblog.com/en/an-important-update-transitioning-gemini-cli-to-antigravity-cli/), [migración](https://www.antigravity.google/docs/cli/gcli-migration), [changelog Antigravity](https://antigravity.google/docs/changelog), [releases Gemini CLI](https://github.com/google-gemini/gemini-cli/releases).

El repo `google-antigravity/antigravity-cli` no publica código fuente (solo README, CHANGELOG y `examples/`) y GitHub no reporta licencia. Hay extensiones de Antigravity para VS Code, Visual Studio, JetBrains, Android Studio, Zed, Xcode, Eclipse y ACP ([ide/extensions](https://antigravity.google/docs/ide/extensions)).

### 1.1 Componentes por Superficie (Antigravity)

| Componente | App | CLI | IDE standalone | Extensiones IDE |
| :--- | :---: | :---: | :---: | :---: |
| Plugins / marketplace | ✅ | ✅ | ✅ (carpetas) | VS Code, VS, Eclipse |
| Skills, rules, MCP, subagentes | ✅ | ✅ | ✅ | ✅ (rules solo VS Code, VS, Eclipse) |
| Hooks | ✅ (doc; arreglos desde 2.6.0) | ✅ (confirmado) | ❓ doc dice sí, sin verificar | doc dice sí |
| Sidecars (procesos de fondo) | **Solo App** | ❌ | ❌ | ❌ |
| Statusline y título por script | ❌ | **Solo CLI** | ❌ | ❌ |
| Workflows legados `.md` | ❌ | ❌ | **Solo IDE** (deprecados) | ❌ |
| Paneles de UI de plugins | **Solo App** | ❌ | ❌ | ❌ |

Gemini CLI tiene una sola superficie (TUI de Ink) y un "IDE companion" para VS Code que solo aporta diff nativo y contexto ([ide-integration](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/ide-integration/index.md)).

---

## 2. Modelo de Extensión

### 2.1 Antigravity

Un **plugin** es una carpeta con `plugin.json` que agrupa skills, MCP, agentes, rules, hooks, sidecars y `bin/`. Las customizaciones sueltas usan el mismo layout dentro de `.agents/` (workspace) o `~/.gemini/config/` (personal) ([customization-discovery](https://antigravity.google/docs/customization-discovery)).

| Customización | Ruta dentro de `.agents/` o `~/.gemini/config/` |
| :--- | :--- |
| Rules | `rules/*.md`, `AGENTS.md`, `GEMINI.md` |
| Skills | `skills/<n>/SKILL.md` |
| Agentes | `agents/<n>.md` o `agents/<n>/agent.md` |
| Plugins | `plugins/<carpeta>/` |
| Hooks | `hooks.json` |
| MCP | `mcp_config.json` |

- Alias de carpeta: `.agent/`, `_agents/`, `_agent/`. El escaneo baja un solo nivel. Se recorre el workspace y sus padres hasta la raíz del repo.
- Manifiestos de descubrimiento `skills.json`, `rules.json`, `agents.json`, `plugins.json` con `entries`, `inherits`, `exclude`, `include_only` (sin wildcards).
- **Precedencia ante el mismo nombre** (skills y agentes): carpeta personal en el repo (`.gemini/config.json` → `personal_customization_dir: "people/{user}"`) > workspace > `~/.gemini/config/` > built-in. En el CLI, `~/.gemini/antigravity-cli/skills/` y `~/.gemini/skills/` ganan sobre todo. Las rules se acumulan. Las skills de plugin se nombran `<plugin>:<skill>`.
- Plugins: workspace `.agents/plugins/`, global `~/.gemini/config/plugins/`. El CLI instala en `~/.gemini/config/plugins/<n>/` (solo lectura, se reemplaza al actualizar).
- Directorios: settings del CLI `~/.gemini/antigravity-cli/settings.json`; datos App `~/.gemini/antigravity/`; datos IDE `~/.gemini/antigravity-ide/`.
- No hay scope "managed" local documentado; las políticas de admin vienen de Gemini Enterprise (§13).

### 2.2 Gemini CLI

Una **extensión** es una carpeta con `gemini-extension.json` en `~/.gemini/extensions/<n>/` (instalación solo a nivel usuario; habilitación por `user` o `workspace`) ([extensions/reference](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/extensions/reference.md)).

- Settings (menor → mayor): defaults < `system-defaults.json` < `~/.gemini/settings.json` < `.gemini/settings.json` < `/etc/gemini-cli/settings.json` < variables de entorno < flags. Las extensiones quedan por debajo de todo ([configuration](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/reference/configuration.md)). La doc de hooks contradice el orden ("proyecto > usuario > sistema > extensiones").
- Componentes sueltos: `.gemini/commands/`, `.gemini/skills/` o `.agents/skills/`, `.gemini/agents/`, `~/.gemini/policies/`, `~/.gemini/theme.json`, `~/.gemini/keybindings.json`.
- **Folder trust**: en carpetas no confiables bloquea `.env`, MCP, commands, hooks, skills e instalar o actualizar extensiones ([trusted-folders](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/trusted-folders.md)).

---

## 3. Manifiesto

### 3.1 Antigravity: `plugin.json`

Ubicación: `.antigravity-plugin/plugin.json` > `.gemini-plugin/plugin.json` (orden entre ambas no verificado) > `plugin.json` en la raíz. Schema en vivo: [schemas/v1/plugin.json](https://antigravity.google/schemas/v1/plugin.json) (draft 2020-12, `required: ["name"]`).

```json
{
  "$schema": "https://antigravity.google/schemas/v1/plugin.json",
  "name": "mi-plugin",
  "displayName": "Mi Plugin",
  "description": "...",
  "version": "1.0.0",
  "logo": "assets/logo.svg",
  "author": { "name": "...", "email": "...", "url": "..." },
  "suggestedPrompts": ["...", "..."],
  "skills": ["extra-skills/"],
  "homepage": "https://...",
  "repository": "https://...",
  "license": "MIT",
  "keywords": ["..."]
}
```

| Campo | Detalle |
| :--- | :--- |
| `name` | Schema: `^[a-zA-Z0-9_-]+$`; kebab-case para `--strict` y Marketplace. Si falta, se toma el nombre de la carpeta. Prefija el MCP como `<name>_<server>` |
| `displayName` / `display_name` | Gana `displayName`. Sin prefijos "Antigravity", "Gemini", "Claude" ni "Cursor" |
| `description`, `version` | `version` libre; semver recomendado para detectar updates |
| `disabled` | Estado inicial; la elección del usuario prevalece |
| `logo` | Ruta relativa .png/.svg/.jpg/.webp, o URL https de google.com, gstatic.com o googleusercontent.com |
| `suggestedPrompts` | Máximo 3 |
| `skills` | Carpetas extra además de `skills/` |
| `extensions` | `{namespace: {...}}` "client-specific settings"; uso no documentado |
| `source`, `sourcePath` | Deprecados |

- **Claves desconocidas:** se ignoran y `agy plugin validate` las reporta (warning; error solo con `--strict`). Una clave conocida con tipo incorrecto impide cargar el plugin.
- **Sin campos** para `userConfig`, `commands`, `agents`, `hooks`, `mcpServers` inline, `lspServers`, `outputStyles` ni `dependencies`: las rutas de componentes son fijas.
- **Validación:** `agy plugin validate [--strict] [--json] <dir>`. Niveles Error / Warning / Note. Revisa manifiesto, frontmatter, `mcp_config.json`, `hooks.json` (eventos desconocidos, regex inválidas, scripts faltantes), `sidecar.json` y manifiestos de lista. **Advierte sobre `hooks/hooks.json`, `commands/`, `workflows/` y una clave `"hooks"` estilo Claude en la raíz** (se cargaría como un único hook llamado `hooks`) ([build-plugins](https://antigravity.google/docs/build-plugins)).
- **Repos multi-herramienta:** `.mcp.json`, `mcp.json`, `AGENTS.md` en raíz y un `plugin.json` raíz sombreado se ignoran "by design"; la doc incluye un filtro `jq` para CI.
- **`.claude-plugin/`:** no se lee como manifiesto nativo, pero convive. `agy plugin install` sí procesa plugins de Claude (CLI 1.5.6 corrige que ignoraba las carpetas `skills` del manifiesto de Claude).
- **Agent Plugins 1.0:** sin soporte declarado. Un manifiesto mínimo pasaría el schema, pero `mcp.json` se ignora (Antigravity usa `mcp_config.json`): solo funcionan las skills ([blog](https://developers.googleblog.com/agent-plugins-package-your-skills-tools-and-more/)).

### 3.2 Gemini CLI: `gemini-extension.json`

Campos (`ExtensionConfig`, `packages/cli/src/config/extension.ts`): `name` (minúsculas con guiones, = carpeta), `version`, `description`, `mcpServers`, `contextFileName` (string o string[]), `excludeTools`, `settings[]`, `themes[]`, `plan.directory`, `migratedTo`.

- Variables: `${extensionPath}`, `${workspacePath}`, `${/}`, y del entorno `$VAR`, `${VAR}`, `${VAR:-def}`.
- `gemini extensions validate` existe en el código (semver, `contextFileName`, URLs de MCP) aunque no en la doc.
- Sin compatibilidad con `.claude-plugin` ni Agent Plugins 1.0.

---

## 4. Hooks

### 4.1 Antigravity

Fuente: [docs/hooks](https://antigravity.google/docs/hooks).

**Archivos:** `.agents/hooks.json`, `~/.gemini/config/hooks.json`, `<plugin>/hooks.json` en la **raíz**. Los agentes custom también pueden declarar `hooks:` en su frontmatter (App 2.17.0). La clave `hooks` de `~/.gemini/antigravity-cli/settings.json` existe pero no se carga.

**Formato** (indexado por **nombre de hook**, no por evento):

```json
{
  "mi-guard": {
    "enabled": true,
    "PreToolUse": [
      { "matcher": "run_command", "hooks": [{ "type": "command", "command": "python3 ./hooks/guard.py", "timeout": 30 }] }
    ],
    "Stop": [{ "type": "command", "command": "./hooks/on-stop.sh" }]
  }
}
```

- `PreToolUse`/`PostToolUse` usan `{matcher, hooks: [...]}`; los demás eventos, lista plana.
- `type: "command"` es el único documentado; `timeout` en **segundos** (30). El changelog menciona "prompt hooks" / "hooks that call a model" sin formato documentado.
- Matcher: regex sobre el nombre del tool (`""` o `*` = todos).

**stdin común** (camelCase): `conversationId`, `workspacePaths[]`, `transcriptPath`, `artifactDirectoryPath`, `modelName`, `lastUserInput`. Desde CLI 1.5.4 también `hookEventName`, `cwd` y `toolCall.id`.

| Evento | Momento | stdin extra | stdout / decisiones |
| :--- | :--- | :--- | :--- |
| `SessionStart` | Inicio de conversación | — | `injectSteps[]` |
| `PreInvocation` | Antes de cada llamada al modelo | `invocationNum`, `initialNumSteps` | `injectSteps[]` (`systemMessage`, `userMessage`, `ephemeralMessage`, `errorMessage`); `terminationBehavior: "terminate"` bloquea el prompt (CLI 1.4.11) |
| `PostInvocation` | Después de cada invocación | igual | `injectSteps`, `terminationBehavior: "force_continue" \| "terminate"` |
| `PreToolUse` | Antes del tool | `toolCall{name,args}`, `stepIdx` | `decision` (tabla abajo), `reason`, `overwrite` (merge superficial sobre args), `permissionOverrides[]` |
| `PostToolUse` | Después del tool | `toolCall`, `stepIdx`, `error` (**sin output**) | `{}` |
| `Stop` | Fin del loop | `executionNum`, `terminationReason`, `error`, `fullyIdle`, `continuation_active` | `decision: "continue"` + `reason` (límite de continuaciones) |

| `decision` (`PreToolUse`) | Significado |
| :--- | :--- |
| `allow` | Pasa al siguiente hook o a la política |
| `auto_approve` | Aprueba cuando la política iba a preguntar (no salta una revisión impuesta por admin) |
| `deny` | Bloqueo duro |
| `ask` | Pregunta respetando "Always Allow" |
| `force_ask` | Pregunta siempre |
| `deny_unless_prior_grant` | Bloquea salvo concesión previa |

> [!CAUTION]
> **No existe bloqueo por código de salida.** El hook debe salir con 0 e imprimir JSON; **cualquier otro código se registra y se ignora (falla abierto)**. Un script portado de Claude que bloquea con `exit 2` no bloquea nada en Antigravity. Comportamiento ante timeout: no documentado.

**Proceso:** hooks de plugin con cwd = raíz del plugin y variables de entorno `PLUGIN_ROOT` y `PLUGIN_DATA` (CLI ≥ 1.5.3; el placeholder `${PLUGIN_ROOT}` no se expande dentro de `command`, por eso se recomiendan rutas relativas). En Windows el comando va a `cmd.exe` tal cual.

**Confianza:** Workspace Trust pregunta antes de cargar skills, hooks y modos de auto-aprobación del repo (`trustedWorkspaces`). `.agents/plugins.json`/`skills.json` solo cargan rutas dentro del repo. Un plugin deshabilitado no corre hooks. Los hooks no corren en `/btw`. `/hooks` lista y edita (guarda en el archivo global).

**Tools y argumentos** (PascalCase):

| Propósito | Tool | Argumentos |
| :--- | :--- | :--- |
| Shell | `run_command` | `CommandLine`, `Cwd`, `WaitMsBeforeAsync`, `RunPersistent`, `RequestedTerminalID` |
| Lectura | `view_file` | `AbsolutePath`, `StartLine`, `EndLine`, `IsSkillFile` |
| Escritura | `write_to_file` | `TargetFile`, `Overwrite`, `CodeContent`, `Description`, `IsArtifact`, `ArtifactMetadata` |
| Edición | `replace_file_content` | `TargetFile`, `TargetContent`, `ReplacementContent`, `AllowMultiple`, `StartLine`, `EndLine`, `Instruction` |
| Multi-edición | `multi_replace_file_content` | `TargetFile`, `ReplacementChunks[]` (campos no documentados) |
| Otros | `grep_search`, `find_by_name`, `list_dir`, `read_url_content`, `search_web`, `browser_*`, `invoke_subagent`, `call_mcp_tool`, `manage_task`, `schedule`, `ask_permission`, `list_permissions` | |

### 4.2 Gemini CLI

Fuentes: [hooks/reference](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/hooks/reference.md), [blog](https://developers.googleblog.com/en/tailor-gemini-cli-to-your-workflow-with-hooks/).

- **Configuración:** clave `hooks` en `settings.json` (usuario, proyecto, sistema). En extensiones: `hooks/hooks.json` **envuelto en `{"hooks": {...}}`** como en Claude (si no, se descarta con warning; `extension-manager.ts`).
- Definición: `matcher` (regex para tools; string exacto para ciclo de vida), `sequential`. Handler: `type: "command"`, `command`, `name`, `timeout` en **ms** (60000), `description`.
- Activación global `hooksConfig.enabled`, `hooksConfig.disabled[]`; `/hooks panel|enable|disable`.
- **stdin base:** `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `timestamp`. **stdout común:** `systemMessage`, `suppressOutput`, `continue`, `stopReason`, `decision` (allow | deny/block), `reason`.
- **Códigos:** 0 = parsear JSON; **2 = bloqueo** con stderr como motivo; otro = warning y continúa; stdout no JSON = allow (texto como `systemMessage`).

| Evento | Payload | Salida relevante |
| :--- | :--- | :--- |
| `SessionStart` | `source`: startup, resume, clear | `additionalContext`, `systemMessage` |
| `SessionEnd` | `reason` | Best-effort |
| `BeforeAgent` | `prompt` | `deny` (descarta el mensaje), `continue:false`, `additionalContext` |
| `AfterAgent` | `prompt`, `prompt_response`, `stop_hook_active` | `deny` = reintentar con `reason`; `clearContext` |
| `BeforeModel` | `llm_request` | Reescribe la request o devuelve `llm_response` sintético |
| `AfterModel` | `llm_request`, `llm_response` por chunk | Reemplaza el chunk |
| `BeforeToolSelection` | `llm_request` | `toolConfig.mode`, `allowedFunctionNames` |
| `BeforeTool` | `tool_name`, `tool_input`, `mcp_context`, `original_request_name` | `deny` + `reason`, `hookSpecificOutput.tool_input`, `continue:false`. **`ask` sin efecto** ([coreToolHookTriggers.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/core/coreToolHookTriggers.ts)) |
| `AfterTool` | + `tool_response` | `deny` oculta el resultado, `additionalContext`, `tailToolCallRequest` |
| `PreCompress` | `trigger`: auto, manual | Consultivo |
| `Notification` | `notification_type: "ToolPermission"`, `message`, `details` | Observación |

- **Entorno** (saneado): `GEMINI_PROJECT_DIR`, `GEMINI_PLANS_DIR`, `GEMINI_SESSION_ID`, `GEMINI_CWD`, **`CLAUDE_PROJECT_DIR` como alias**; los hooks de extensión reciben los `settings[].envVar` de la extensión.
- **Confianza:** hooks de proyecto identificados por huella (aviso si cambian) + folder trust.
- **Tools:** `run_shell_command` (`command`, `dir_path`, `is_background`), `read_file` (`file_path`), `read_many_files` (`include`…), `write_file` (`file_path`, `content`), `replace` (`file_path`, `old_string`, `new_string`), `grep_search`, `glob`, `list_directory`, `web_fetch`, MCP `mcp_<server>_<tool>` ([tools](https://geminicli.com/docs/reference/tools/)).
- **Migración:** `gemini hooks migrate --from-claude` lee solo `.claude/settings*.json` (no hooks de plugins), escribe `.gemini/settings.json`, mapea eventos y tools, convierte timeouts a ms y `$CLAUDE_PROJECT_DIR` → `$GEMINI_PROJECT_DIR`. No traduce stdin/stdout ni `${CLAUDE_PLUGIN_ROOT}`, y escribe `grep`/`ls` en vez de `grep_search`/`list_directory` ([migrate.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/cli/src/commands/hooks/migrate.ts)).

### 4.3 Mapeo con Claude Code

| Claude Code | Antigravity | Gemini CLI |
| :--- | :--- | :--- |
| `SessionStart` | `SessionStart` (sin `source`) | `SessionStart` (startup/resume/clear) |
| `UserPromptSubmit` | `PreInvocation` (aprox.: usar `invocationNum == 0`; bloquea con `terminate`) | `BeforeAgent` |
| `PreToolUse` | `PreToolUse` (bloqueo solo por JSON) | `BeforeTool` (`exit 2` sirve; `ask` no) |
| `PostToolUse` | `PostToolUse` (sin output ni inyección) | `AfterTool` |
| `Stop` | `Stop` (`"continue"` ≈ `block` de Claude) | `AfterAgent` |
| `SubagentStop` | — | `AfterAgent` |
| `PreCompact` | — | `PreCompress` |
| `Notification` | — | `Notification` (solo ToolPermission) |
| `SessionEnd` | — | `SessionEnd` |
| `PermissionRequest` | — | — |
| (sin equivalente) | `PostInvocation` | `BeforeModel`, `AfterModel`, `BeforeToolSelection` |

---

## 5. Skills

| Aspecto | Antigravity | Gemini CLI |
| :--- | :--- | :--- |
| Ubicaciones | `.agents/skills/<n>/` (y padres; legado `.agent/skills`), `~/.gemini/config/skills/`, `<plugin>/skills/`; solo CLI: `~/.gemini/antigravity-cli/skills/`, `~/.gemini/skills/` | built-in < extensión < usuario (`~/.gemini/skills`, `~/.agents/skills`) < workspace (`.gemini/skills`, `.agents/skills`) |
| Frontmatter | `name` (en plugins = carpeta, 1–64), `description` (1–1024), `disable-model-invocation` (se respeta), `license`, `compatibility`. `allowed-tools`, `model`, `argument-hint`, `context`, `agent`, `hooks` no documentados | **Solo `name` y `description`** (`skillLoader.ts`) |
| Invocación | Automática por descripción o `/<nombre>` (`<plugin>:<skill>`); `/skills`, `/skills reload`; se releen cada turno | Tool `activate_skill` **con confirmación del usuario**; `/skills list\|enable\|disable\|link\|reload`, `gemini skills install\|uninstall` |

Fuentes: [antigravity/skills](https://antigravity.google/docs/skills), [gemini/skills](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/skills.md).

---

## 6. Comandos Slash y Workflows

### 6.1 Antigravity

- **No hay `commands/`**: cada skill es un comando `/nombre`. Un plugin con `commands/` genera warning y no se carga.
- `agy plugin import` convierte commands de Claude y Gemini en skills (CLI 1.5.5).
- Argumentos: sin sintaxis `$ARGUMENTS` documentada; el texto tras `/skill` va al modelo.
- **Workflows legados** (solo IDE, deprecados): `.agents/workflows/<n>.md`, `~/.gemini/config/global_workflows/`, máx. 12 000 caracteres, `/nombre`; una skill homónima gana. `/migrate-workflows` en App ≥ 2.12.0 ([workflows-to-skills](https://antigravity.google/docs/migration/workflows-to-skills)).
- "Dynamic Workflows" (`/run-workflow`): scripts Python generados por el agente, no un componente de plugin ([dynamic-workflows](https://antigravity.google/docs/dynamic-workflows)).

### 6.2 Gemini CLI

Fuente: [custom-commands](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/custom-commands.md).

```toml
# .gemini/commands/git/commit.toml  ->  /git:commit
description = "Genera un commit"
prompt = """
Analiza los cambios: !{git diff --cached}
Contexto extra: {{args}}
"""
```

- TOML en `~/.gemini/commands/`, `.gemini/commands/`, `<ext>/commands/`. Campos `prompt` (obligatorio), `description`.
- `{{args}}` (escapado dentro de `!{...}`); sin `{{args}}`, el texto se agrega al final. `!{cmd}` ejecuta con confirmación; `@{ruta}` inyecta archivos.
- Subcarpetas con `:` (`/git:commit`). Proyecto gana sobre usuario; en conflicto el de la extensión pasa a `/ext.cmd`. `/commands reload|list`.

---

## 7. Subagentes

### 7.1 Antigravity

Fuente: [subagents](https://antigravity.google/docs/subagents).

| Aspecto | Detalle |
| :--- | :--- |
| Ubicaciones | `.agents/agents/<n>.md` o `<n>/agent.md`, `~/.gemini/config/agents/`, `<plugin>/agents/` |
| Frontmatter | `name`, `description`, `mainAgent`, `subagent`, `model` (`inherit`, `flash_lite`, `flash`, `pro`: tiers, no IDs), `tools[]` (`send_message` siempre incluido; un nombre mal escrito puede colgar al agente), `commandExecutionPolicy` (`off`, `auto`, `eager`, `sandbox`), `mcpServers[]`, `skills[]`, `plugins[]`, `rules`, `agents`, `hooks`, `inheritMcp`, `inheritCustomizations`, `excludeDefaultComponents` |
| Ejecución | Tool `invoke_subagent`, workspace `inherit`, `branch` (worktree) o `share`; profundidad máx. 10; herencia de permisos; `agy agents`, `agy --agent <n>` como agente principal |

### 7.2 Gemini CLI

Fuente: [subagents](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/core/subagents.md). Ubicaciones `.gemini/agents/*.md`, `~/.gemini/agents/*.md`, `<ext>/agents/` (habilitados por defecto con `enableAgents`). Frontmatter: `name` (se expone como tool), `description`, `kind` (`local` o `remote` vía A2A), `tools[]` (comodines `*`, `mcp_*`, `mcp_<srv>_*`; omitido = hereda todo), `mcpServers` (inline y aislados), `model` (ID real), `temperature`, `max_turns` (30), `timeout_mins` (10). `/agents list|reload|enable|disable|config`.

---

## 8. MCP

| Aspecto | Antigravity | Gemini CLI |
| :--- | :--- | :--- |
| Archivos | `~/.gemini/config/mcp_config.json`, `.agents/mcp_config.json`, `<plugin>/mcp_config.json`. **No lee `.mcp.json`** | `mcpServers` en `settings.json` o en el manifiesto de la extensión (gana `settings.json`) |
| Transportes | stdio; Streamable HTTP (`serverUrl` o `url` + `headers`). **Sin SSE ni `httpUrl`** | stdio, SSE (`url`), Streamable HTTP (`httpUrl`) |
| Campos extra | `authProviderType: "google_credentials"`, `oauth{clientId,clientSecret}`, `disabled`, `enabledTools`, `disabledTools`, `tools.<t>.eager`, `tools.<t>.background`, `timeoutSeconds` (180/300; −1 sin límite) | `headers`, `env`, `cwd`, `timeout` (600 000 ms), `trust` (**prohibido desde extensión**), `includeTools`, `excludeTools`, `authProviderType`, `targetAudience`, `targetServiceAccount` |
| Carga | **Diferida por defecto**: schemas a disco, se invocan vía `call_mcp_tool`; con `eager` se registran como `mcp_<srv>_<tool>` | `mcp_<srv>_<tool>` |
| OAuth | PKCE con CIMD (`https://antigravity.google/oauth/client-metadata.json`) > DCR > manual; tokens en `~/.gemini/antigravity/mcp_oauth_tokens.json` | Descubrimiento automático o `oauth`; `/mcp auth` |
| Desde plugin | Nombre `<plugin>_<server>`; `${PLUGIN_ROOT}`/`${PLUGIN_DATA}` en `args`, `cwd`, `env`; `variables[]` (`name`, `title`, `description`, `link`) interpoladas en `command`, `args`, `env`, `url`, `headers`, `oauth`. **Un plugin con `variables` se instala deshabilitado hasta configurarlo** | `mcpServers` del manifiesto + `settings[].envVar` |
| Permisos | `mcp(server/tool)`, `mcp(server/*)`, `mcp(*)`; ask por defecto | Allowlist de admin |

Fuentes: [antigravity/mcp](https://antigravity.google/docs/mcp), [gemini/mcp-server](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/tools/mcp-server.md).

---

## 9. Reglas e Instrucciones

### 9.1 Antigravity

Fuente: [rules](https://antigravity.google/docs/rules).

- Workspace y padres: `AGENTS.md`, `GEMINI.md`, `.agents/AGENTS.md`/`GEMINI.md`, `.agents/rules/*.md` (legado `.agent/rules`).
- Global: `~/.gemini/AGENTS.md`, `~/.gemini/GEMINI.md`, `~/.gemini/config/AGENTS.md`/`GEMINI.md`, `~/.gemini/config/rules/*.md`.
- **`CLAUDE.md` no se lee.**
- Frontmatter: `trigger` **obligatorio** (`always_on`, `model_decision`, `glob`, `manual`); **un trigger inválido descarta la regla en silencio**. También `description` y `globs` (separados por comas). `manual` usa `@mención` (el CLI no la soporta).
- Includes `@[label](ruta)`; `rules.json` para reglas anidadas o compartidas.
- Límites: 24 KB por archivo; 20 000 tokens compartidos (si se supera, las más grandes se degradan a punteros).
- **Desde plugin:** `rules/AGENTS.md` o `rules/GEMINI.md` (siempre activas) y `rules/<n>.md` con trigger. **Un `AGENTS.md` en la raíz del plugin se ignora.**

### 9.2 Gemini CLI

Fuente: [gemini-md](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/gemini-md.md). `~/.gemini/GEMINI.md` + workspace y padres + JIT (al tocar un directorio busca hacia arriba), concatenados. Imports `@archivo.md`; `/memory show|reload`. `context.fileName` (string o array) permite apuntar a `AGENTS.md` o `CLAUDE.md`. Extensión: `contextFileName` o `GEMINI.md` en su raíz. Override total del system prompt con `GEMINI_SYSTEM_MD` (solo entorno).

---

## 10. Configuración del Plugin y Datos

| Claude Code | Antigravity | Gemini CLI |
| :--- | :--- | :--- |
| `userConfig` / secretos | Solo `mcpServers.<srv>.variables` (diálogo al habilitar; sin tipos, defaults ni `sensitive` documentados; no verificado si llegan a hooks o skills) | **`settings[]`**: `name`, `description`, `envVar`, `sensitive`. Se piden al instalar (`--skip-settings`), se guardan en el `.env` de la extensión; `sensitive` → **keychain**. `gemini extensions config <n> [setting] [--scope]`. Se inyectan en MCP y hooks |
| `${CLAUDE_PLUGIN_ROOT}` | `PLUGIN_ROOT` (env en hooks ≥ 1.5.3; placeholder en MCP; cwd del hook) | `${extensionPath}` |
| `${CLAUDE_PLUGIN_DATA}` | `PLUGIN_DATA`: carpeta escribible fuera del workspace, persistente entre updates (ruta no documentada) | — (`plan.directory` para planes) |
| `bin/` | **`bin/`**: herramientas que el agente "puede correr por nombre" (CLI 1.3.2); alcance no verificado | — |
| Settings por defecto | No existen. Scripts en `scripts/` para conservar 0755 en el bundle del Marketplace | `excludeTools`, `plan`, políticas TOML (sin `allow`) |

La extensión de Gemini **no hereda el entorno completo**: solo variables seguras más las declaradas.

---

## 11. Mods de Interfaz

| Mod | Antigravity | Gemini CLI |
| :--- | :--- | :--- |
| Temas | CLI `colorScheme` (8 fijos + estacional); App Settings > Appearance. **Un plugin no aporta temas** | **Una extensión aporta temas** (`themes[]`, se seleccionan como `nombre (extensión)`); también `ui.customThemes`, `~/.gemini/theme.json` ([themes](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/themes.md)) |
| Statusline | **Solo CLI:** `statusLine{type: "command", command, padding, enabled, stack_with_default}`. stdin **casi idéntico al de Claude Code** (`session_id`, `cwd`, `transcript_path`, `model{id, display_name, effort}`, `workspace{current_dir, project_dir}`, `version`, `context_window`, `cost{total_usd}`, `agent_state`, `subagents[]`, `quota`, `vim`…). Máx. 1 ejecución/300 ms, timeout 5 s, se apaga tras 30 fallos. `title` igual para el título de la terminal. Lo configura el usuario, no un plugin ([cli/statusline](https://antigravity.google/docs/cli/statusline)) | `ui.footer.items`, `showLabels`, flags `hide*`. **Sin statusline por script** |
| Atajos | `~/.gemini/antigravity-cli/keybindings.json` (`{acción: [teclas]}`); App Shortcuts. No desde plugin | `~/.gemini/keybindings.json` |
| Notificaciones | `notifications` en el CLI (campana, escritorio) | `general.enableNotifications`, `notificationMethod` + hook `Notification` |
| Output styles | No; lo más cercano es un agente `mainAgent` con `excludeDefaultComponents` | No; `GEMINI_SYSTEM_MD` |
| UI propia | **Paneles de plugin (solo App 2.22.0+)**: vistas en "Extensions" del Overview, páginas completas, visores de archivo de plugins y apps MCP, proyectos gestionados por plugins ([side-panel](https://antigravity.google/docs/side-panel)). **Formato de creación no documentado** | `ui.customWittyPhrases`, `ui.loadingPhrases`, título dinámico |
| Extensiones del editor | IDE fork de VS Code con **Open VSX** por defecto (reportes de 429; se pueden cambiar las URLs del gallery o instalar `.vsix`, fuentes comunitarias). **Sin API documentada que exponga extensiones al agente** | IDE companion sin API |

---

## 12. LSP y Segundo Plano

- **LSP:** ninguno de los dos tiene componente LSP para plugins (el "language server" del changelog de Antigravity es su backend).
- **Sidecars (Antigravity, solo App)** ([sidecars](https://antigravity.google/docs/sidecars)):
  - `~/.gemini/config/sidecars/<id>/sidecar.json` o **`<plugin>/sidecars/<n>/sidecar.json`** (id `<plugin>/<n>`).
  - Campos: `command` o `builtin: "schedule"` (cron, `CRON_TZ=`), `args`, `restart_policy` (`always`, `on-failure`, `never`), `env`, `description`, `display_name`.
  - **Deshabilitados hasta activarlos** en `~/.gemini/config/config.json` → `sidecars.<id>.enabled`.
  - Datos en `~/.gemini/antigravity/sidecar_data/<id>/{data,logs,events}` (`ANTIGRAVITY_EXECUTABLE_DATA_DIR`); CLI `agentapi` en el PATH (`new-conversation`, `send-message`). Las Automations son sidecars con `arguments` y `agent_permissions`.
  - Además: tools `manage_task`, `schedule`, `run_command` con `WaitMsBeforeAsync`, `/tasks`, `/schedule`, MCP `tools.<t>.background: "always"`.
- **Gemini CLI:** sin monitores; solo `run_shell_command` con `is_background`.

---

## 13. Distribución

### 13.1 Antigravity

| Aspecto | Detalle |
| :--- | :--- |
| Marketplace | Solo el oficial `antigravity-plugins-official`, **curado**; publicación por [formulario de partners](https://forms.gle/2EX5RFYPoJe1UgxR9). Sin marketplaces de terceros |
| Instalación | `agy plugin install <n>[@antigravity-plugins-official] \| <github-url> \| <dir>`, `/plugin` (Discover/Installed), App → Customizations → Marketplace, o copiar a `.agents/plugins/` / `~/.gemini/config/plugins/`. `list [--json]`, `enable`, `disable`, `uninstall`, `validate`. Atómica con rollback; rechaza symlinks que salen del plugin; App y CLI se sincronizan |
| Actualizaciones | Auto-update para plugins del Marketplace. **No existe `agy plugin update`**; instalados desde GitHub: no verificado |
| Import desde Claude | `agy plugin import claude`: lee los plugins instalados de Claude Code (omite deshabilitados y de scope proyecto); `commands/*.md` → skills (tolera `argument-hint`); `hooks/hooks.json` → convierte **PreToolUse, PostToolUse, Stop, SessionStart (solo startup)**, mapea tools (`Bash` → `run_command`…), timeout 60 s, warnings para el resto. **No traduce stdin/stdout de los scripts** (no mencionado). MCP de `.mcp.json` (con o sin wrapper, `type: "http"`), expandiendo `${CLAUDE_PLUGIN_ROOT}`; SSE → warning. Copia la carpeta completa |
| Import desde Gemini | Commands TOML → skills, `mcpServers` → `mcp_config.json`, skills y agentes. Hooks, temas, policies y `settings`: no verificado |
| Import asistido | App 2.30: pedirle al agente que importe la config de Claude Code, Codex o Cursor a un plugin propio |
| Enterprise | Políticas de Gemini Enterprise (consola): auto-ejecución de terminal, acceso fuera del workspace (`DENY`, `ALWAYS_ASK`, `NOT_ENFORCED`), MCP custom y `mcpAllowlist`, navegador, umbrales de permisos, modelos, paneles de UI de plugins ([enterprise](https://antigravity.google/docs/enterprise)). Sin "managed plugins" |

### 13.2 Gemini CLI

| Aspecto | Detalle |
| :--- | :--- |
| Galería | [geminicli.com/extensions/browse](https://geminicli.com/extensions/browse/): indexa automáticamente repos públicos con topic `gemini-cli-extension` y manifiesto en la raíz ([releasing](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/extensions/releasing.md)) |
| Instalación | `gemini extensions install <git\|ruta> [--ref] [--auto-update] [--pre-release] [--consent] [--skip-settings]`, `link`, `new <path> [template]`, `uninstall`, `enable`/`disable` (`--scope`), `update [--all]`, `config`. Cambios aplican al reiniciar |
| Releases | Rama o tag de git, o GitHub Releases ("Latest"; archivos por plataforma). `migratedTo` mueve la fuente |
| Enterprise | System settings, admin controls inmutables (Strict mode, Extensions on/off, MCP on/off y allowlist) y políticas TOML de tier admin ([enterprise-controls](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/admin/enterprise-controls.md)) |

**Policy engine** ([policy-engine](https://geminicli.com/docs/reference/policy-engine/)): `[[rule]]` TOML con `toolName`, `commandPrefix`, `commandRegex`, `argsPattern`, `decision` = `allow|deny|ask_user`, `priority`, `denyMessage`, `modes`, `allowRedirection`. `ask_user` = `deny` en headless; las decisiones `allow` de extensiones se ignoran.

---

## 14. Compatibilidad Directa con Plugins de Claude Code

- **Antigravity:** no carga un plugin de Claude tal cual; usa `agy plugin import claude` (o `install`) con conversión parcial. **El contrato de los scripts de hooks no es compatible**: stdin camelCase con `toolCall.args` en PascalCase, bloqueo solo con JSON `decision: "deny"` + `exit 0`; un `exit 2` deja pasar el tool. Un plugin dual necesita `.antigravity-plugin/plugin.json`, `hooks.json` en la raíz indexado por nombre, `mcp_config.json`, `rules/` con trigger y scripts que detecten el host por la forma del stdin.
- **Gemini CLI:** no importa plugins. Compatible: el mismo `hooks/hooks.json` envuelto en `"hooks"` (cambian nombres de evento y tool; timeout en ms), `exit 2`, `CLAUDE_PROJECT_DIR`, skills (solo `name`/`description`), `gemini hooks migrate`. Los comandos `.md` se reescriben en TOML.

---

## 15. Tablas de Compatibilidad por Componente

### 15.1 Antigravity

| Claude Code | Antigravity | ¿Soportado? | Notas |
| :--- | :--- | :---: | :--- |
| `.claude-plugin/plugin.json` | `.antigravity-plugin/` / `.gemini-plugin/` / `plugin.json` | Parcial | Otro schema; el import lee manifiestos de Claude |
| `userConfig` | `mcpServers.<s>.variables` | Parcial | Solo MCP |
| Skills | `skills/SKILL.md` | Sí | `disable-model-invocation` sí |
| Comandos | Skills (`/nombre`) | Parcial | `commands/` no se carga; el import convierte |
| Subagentes | `agents/*.md` | Sí | Frontmatter distinto (`model` por tier) |
| Hooks | `hooks.json` raíz | Parcial | 6 eventos; formato por nombre; exit ≠ 0 falla abierto |
| `PreToolUse` ask/deny/modificar | `decision`, `overwrite` | Sí (más rico) | `force_ask`, `deny_unless_prior_grant` |
| `additionalContext` en `PreToolUse` | — | No | Solo `reason` |
| MCP | `mcp_config.json` | Sí | Sin SSE; prefijo `<plugin>_` |
| LSP | — | No | |
| Monitores | Sidecars | Parcial | Solo App; requiere habilitar |
| `bin/` | `bin/` | Parcial | Alcance no verificado |
| Settings por defecto | — | No | |
| Output styles | Agente `mainAgent` | Aprox. | |
| Temas | — | No (desde plugin) | |
| Statusline | `statusLine` del CLI | Parcial | JSON casi idéntico; no desde plugin |
| `${CLAUDE_PLUGIN_ROOT}` / `_DATA` | `PLUGIN_ROOT` / `PLUGIN_DATA` | Sí | Env en hooks desde 1.5.3 |
| `CLAUDE.md` / reglas | `rules/AGENTS.md` + rules con trigger | Parcial | `CLAUDE.md` no se lee |
| Marketplaces | Solo oficial | Parcial | GitHub o local sí |
| Actualizaciones | Auto-update del Marketplace | Parcial | Sin `update` |
| Scopes | Global / workspace (+ personal) | Parcial | Sin local ni managed |
| UI propia | Paneles de plugin (App) | Sin documentar | |

### 15.2 Gemini CLI

| Claude Code | Gemini CLI | ¿Soportado? | Notas |
| :--- | :--- | :---: | :--- |
| `plugin.json` | `gemini-extension.json` | Parcial | Ruta fija `~/.gemini/extensions` |
| `userConfig` + secretos | `settings[]` (`sensitive` → keychain) | Sí | Inyectado en MCP y hooks |
| Skills | `skills/` | Parcial | Solo `name`/`description`; activación con consentimiento |
| Comandos | `commands/*.toml` | Parcial | Reescritura a TOML |
| Subagentes | `agents/*.md` | Sí | Campos propios |
| Hooks | `hooks/hooks.json` (`{"hooks":{}}`) | Sí (parcial) | 11 eventos; `exit 2`; sin `ask`; ms |
| MCP | `mcpServers` | Sí | `trust` prohibido desde extensión |
| LSP, monitores, `bin/`, `PLUGIN_DATA` | — | No | |
| Settings por defecto | `excludeTools`, `plan`, policies | Parcial | |
| Output styles | — | No | |
| Temas | `themes[]` | Sí | |
| Statusline | `ui.footer.*` | No (sin script) | |
| `${CLAUDE_PLUGIN_ROOT}` | `${extensionPath}` | Sí | |
| `CLAUDE.md` | `GEMINI.md` / `context.fileName` | Parcial | Configurable |
| Marketplace | Galería por topic | Sí | |
| Actualizaciones | `extensions update`, `--auto-update` | Sí | |
| Managed | System settings + admin controls | Sí | |

---

## 16. No Verificado

- Si los hooks se disparan en el **IDE standalone** (la App los arregló desde 2.6.0; el [hilo del foro](https://discuss.ai.google.dev/t/do-antigravity-ide-2-0-actually-execute-plugin-hooks-pretooluse-posttooluse-or-is-that-cli-only-right-now/176814) sigue sin respuesta de Google).
- Formato de los hooks tipo prompt/modelo de Antigravity.
- Formato para crear paneles de UI de plugin y uso de la clave `extensions` del manifiesto.
- Alcance de `bin/` (PATH, superficies).
- Ruta de `PLUGIN_DATA`, dónde se guardan las `variables` MCP y si llegan a hooks o skills.
- Si `agy plugin install` sobre un plugin de Claude convierte lo mismo que `import`; qué pasa con hooks, temas y `settings` de extensiones Gemini al importar.
- Si los campos de skill de Claude se ignoran en silencio en Antigravity.
- Timeout de hooks en Antigravity y campos de `ReplacementChunks`.
- Precedencia entre `.gemini-plugin/` y `.antigravity-plugin/`.
- Precedencia real de hooks de sistema vs. proyecto en Gemini CLI (la doc se contradice).
- Si la allowlist de Open VSX del IDE es configurable oficialmente.
- Soporte futuro de Agent Plugins 1.0 en Antigravity.

> [!NOTE]
> Una [guía de Medium](https://medium.com/google-cloud/a-developers-guide-to-agent-hooks-in-antigravity-cli-4c1440febd11) cita `~/.gemini/antigravity-cli/hooks.json` y "cinco hooks principales", y un post de DEV muestra `.agents/config.json` con `before_tool_call`. Ambos contradicen la documentación oficial y probablemente están desactualizados.
