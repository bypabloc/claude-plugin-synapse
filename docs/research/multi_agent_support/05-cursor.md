# Cursor

> Superficie completa de extensibilidad de Cursor (IDE 3.x, CLI `agent` con versiones por fecha, Cloud Agents y workers self-hosted): plugins, 21 eventos de hook, skills, comandos, subagentes, MCP, reglas, variables, mods de interfaz, distribución y compatibilidad con plugins de Claude Code. Corte al 2026-11-19.

---

## 1. Visión General y Versiones

| Hito | Fecha | Qué agregó |
| :--- | :--- | :--- |
| [1.6](https://cursor.com/changelog/1-6) | 12 sep 2025 | Comandos slash en `.cursor/commands/*.md` |
| [2.0](https://cursor.com/changelog/2-0) | — | Reglas y comandos de equipo desde el dashboard |
| [2.4](https://cursor.com/changelog/2-4) | — | Skills, subagentes, `/migrate-to-skills` |
| [2.5](https://cursor.com/changelog/2-5) | 17 feb 2026 | **Plugins y Marketplace**, `/add-plugin`, subagentes asíncronos y anidados, controles del sandbox |
| [3.0](https://cursor.com/changelog/3-0) | abr 2026 | Agents Window |
| [3.9](https://cursor.com/changelog/customize) | 22 jun 2026 | Página **Customize**: gestiona plugins, skills, MCP, subagentes, reglas, comandos y hooks por scope; canvases de plugin |
| [3.27–3.30](https://cursor.com/docs/release-notes) | oct–nov 2026 | Hooks con `parent_agent_id`/`parent_agent_name`; mejoras de MCP |
| [Agent SDK](https://cursor.com/changelog) | 20 oct 2026 | Agentes como carpeta (`instructions.md`, `tools/`, `skills/`, `mcp-connections/`, `channels/`). Producto aparte, no un plugin |

El CLI usa versiones por fecha (`v2026.11.17`). El binario es `agent`; `cursor-agent` aparece en foros y podría ser un alias (no verificado).

### 1.1 Superficies

| Superficie | Qué soporta |
| :--- | :--- |
| IDE (y Agents Window) | Todo: 21 eventos de hook, plugins, extensiones VS Code, LSP |
| CLI | Hooks (desde ene 2026), plugins (`/plugin`, `agent plugin marketplace add/list/update/remove`, `--plugin-dir`, `enabled_plugins` en `~/.cursor/settings.json`), skills, comandos, subagentes, MCP, `statusLine`. Lee `AGENTS.md` y `CLAUDE.md`. **Desde `v2026.08.11` ejecuta los hooks de plugins instalados**, incluidos los de `--plugin-dir`. [Changelog](https://cursor.com/docs/cli/changelog) |
| Cloud Agents (web, iOS, Slack, GitHub, Linear, API) | Solo hooks de comando y 14 eventos. Sin hooks de usuario (sí team, enterprise y proyecto). Sin hooks `prompt`. MCP desde la config de team. Solo `~/.cursor/skills` se sincroniza, y solo si se habilita. Plugins fijos en la versión con que arrancó el agente. [Capabilities](https://cursor.com/docs/cloud-agent/capabilities) |
| Workers self-hosted | Hooks de proyecto, team y enterprise. `sessionStart`/`sessionEnd` corren al tomar o liberar el worker; `sessionStart` recibe `surface`. |

---

## 2. Modelo de Extensión

```text
+-------------------------------- Cursor ---------------------------------+
| Capa 1: configuración      .cursor/  ~/.cursor/                         |
|         rules/ skills/ agents/ commands/ hooks.json mcp.json cli.json   |
|         + rutas de compatibilidad: .claude/ .codex/ .agents/            |
|                                                                         |
| Capa 2: plugins            .cursor-plugin/plugin.json                   |
|         + Agent Plugins 1.0 (plugin.json en la raíz: solo skills y MCP) |
|                                                                         |
| Capa 3: extensiones VS Code (Open VSX vía marketplace.cursorapi.com)    |
|         temas, LSP, keybindings, APIs vscode.cursor.*                   |
+-------------------------------------------------------------------------+
```

### 2.1 Scopes y Precedencia

| Componente | Precedencia (mayor → menor) |
| :--- | :--- |
| Hooks | Enterprise (MDM) > Team (dashboard, solo Enterprise) > Proyecto > Usuario > hooks de Claude (local > proyecto > usuario). Los de plugins se evalúan al final y solo los de tipo comando. |
| Reglas | Team > Proyecto > Usuario |
| Subagentes | Proyecto > Usuario; `.cursor/` > `.claude/` > `.codex/` |
| Plugins | Scope usuario o proyecto; Customize filtra por usuario, workspace o team |

Fuentes: [hooks](https://cursor.com/docs/agent/hooks), [third-party-hooks](https://cursor.com/docs/reference/third-party-hooks), [rules](https://cursor.com/docs/rules), [subagents](https://cursor.com/docs/subagents).

---

## 3. Manifiesto `.cursor-plugin/plugin.json`

Fuentes: [reference/plugins](https://cursor.com/docs/reference/plugins), [plugins/building](https://cursor.com/docs/plugins/building).

```json
{
  "name": "mi-plugin",
  "description": "...",
  "version": "1.0.0",
  "author": { "name": "...", "email": "..." },
  "homepage": "https://...",
  "repository": "https://...",
  "license": "MIT",
  "keywords": ["..."],
  "logo": "assets/logo.png",
  "rules": "./rules",
  "skills": "./skills",
  "agents": "./agents",
  "commands": ["./commands"],
  "hooks": "./config/cursor-hooks.json",
  "mcpServers": "./mcp.json",
  "variables": {
    "type": "object",
    "properties": { "API_TOKEN": { "type": "string", "description": "..." } }
  }
}
```

| Campo | Regla |
| :--- | :--- |
| `name` | **Único obligatorio.** kebab-case; letras, dígitos, `-` y `.`; empieza y termina en alfanumérico |
| Rutas de componentes (`rules`, `agents`, `skills`, `commands`, `hooks`, `mcpServers`) | String o array (`hooks` acepta config inline; `mcpServers` ruta, objeto o array). **Una ruta declarada reemplaza el descubrimiento por defecto** |
| `variables` | JSON Schema (subconjunto) con los valores que se interpolan como `${VAR}` |

**Descubrimiento por defecto:** `skills/*/SKILL.md`, `rules/*.md|.mdc|.markdown`, `agents/*.md|.mdc|.markdown`, `commands/*.md|.mdc|.markdown|.txt`, `hooks/hooks.json`, `mcp.json`, y `SKILL.md` en la raíz (plugin de un solo skill). Con `vscode.cursor.plugins.registerPath` el manifiesto es opcional.

**Validación:** no hay un equivalente de `claude plugin validate`. El checklist exige rutas relativas sin `..`, frontmatter válido y que cada `${VAR}` de `mcp.json` esté declarado en `variables`.

> [!WARNING]
> **Repo dual Claude + Cursor:** Claude Code carga cualquier `hooks/hooks.json` de la raíz del plugin. Si ese archivo tiene eventos de Cursor, choca. La práctica de la comunidad es poner los hooks de Cursor en otra ruta y declararla en el campo `hooks` del manifiesto de Cursor ([ejemplo](https://github.com/frontleapsoftware/cursor-plugin/pull/8)). No hay documentación oficial de que Cursor lea `.claude-plugin/plugin.json` como manifiesto propio.

---

## 4. Hooks

Fuente: [docs/agent/hooks](https://cursor.com/docs/agent/hooks).

### 4.1 Configuración

```json
{
  "version": 1,
  "hooks": {
    "beforeShellExecution": [
      { "command": "./hooks/guard.sh", "matcher": "rm|git push", "timeout": 30, "failClosed": true }
    ],
    "stop": [
      { "type": "prompt", "prompt": "¿Quedó algo sin terminar?", "loop_limit": 5 }
    ]
  }
}
```

| Campo | Detalle |
| :--- | :--- |
| `command` | Script a ejecutar |
| `type: "prompt"` + `prompt` + `model?` | **Hook evaluado por un LLM** (no disponible en Cloud) |
| `matcher` | Regex (ver §4.5) |
| `timeout` | Segundos; valor por defecto no documentado |
| `failClosed` | `false` por defecto |
| `loop_limit` | Solo `stop`/`subagentStop`; 5 por defecto; `null` sin límite |

**Ubicaciones:** `.cursor/hooks.json` (proyecto), `~/.cursor/hooks.json` (usuario), `/etc/cursor/hooks.json` · `/Library/Application Support/Cursor/hooks.json` · `C:\ProgramData\Cursor\hooks.json` (enterprise MDM), dashboard (team), plugin.

### 4.2 Entrada Común

`conversation_id`, `generation_id`, `model`, `model_id?`, `model_params?`, `hook_event_name`, `cursor_version`, `workspace_roots`, `user_email`, `transcript_path`. Los hooks de subagente agregan `parent_agent_id` y `parent_agent_name`.

### 4.3 Eventos

| Evento | Momento | Entrada propia | Salida / decisión | Cloud |
| :--- | :--- | :--- | :--- | :---: |
| `sessionStart` | Al crear la conversación (fire-and-forget) | `session_id`, `is_background_agent`, `composer_mode` | `env` (se propaga a hooks siguientes), `additional_context` | ❌ |
| `sessionEnd` | Al terminar | `reason`, `duration_ms`, `final_status`, `error_message` | — | ❌ |
| `preToolUse` | Antes de cualquier tool | `tool_name`, `tool_input`, `tool_use_id`, `cwd` | `permission` allow/deny (**`ask` no se aplica**), `updated_input` | ✅ |
| `postToolUse` | Tras tool exitoso | `tool_output`, `duration` | `additional_context`; `updated_mcp_tool_output` (solo MCP) | ✅ |
| `postToolUseFailure` | Tool fallido, timeout o denegado | `error_message`, `failure_type`, `is_interrupt` | `additional_context` | ✅ |
| `subagentStart` | Antes de lanzar un subagente | `subagent_type`, `task`, `subagent_model`, `is_parallel_worker`… | allow/deny (`ask` = deny) | ✅ |
| `subagentStop` | Al terminar un subagente | `status`, `summary`, `modified_files`, `agent_transcript_path`… | `followup_message` (si `completed`) | ✅ |
| `beforeShellExecution` | Antes de shell | `command`, `cwd`, `sandbox` | allow/deny/**ask** | ✅ |
| `afterShellExecution` | Tras shell | `output`, `duration` | — | ✅ |
| `beforeMCPExecution` | Antes de tool MCP | `tool_name`, `tool_input`, `mcp_server_name`, `url`/`command` | allow/deny/ask | ❌ |
| `afterMCPExecution` | Tras tool MCP | `result_json`, `duration` | — | ❌ |
| `beforeReadFile` | Antes de leer | `file_path`, `content`, `attachments` | allow/deny | ✅ |
| `afterFileEdit` | Tras editar | `file_path`, `edits[{old_string,new_string}]` | — | ✅ |
| `beforeSubmitPrompt` | Antes de enviar el prompt | `prompt`, `attachments` | `continue` true/false | ✅ |
| `preCompact` | Antes de compactar | `trigger`, `context_usage_percent`… | Solo observa | ✅ |
| `stop` | Fin del loop | `status`, `loop_count` | `followup_message` (se autoenvía) | ✅ |
| `afterAgentResponse` / `afterAgentThought` | Al cerrar mensaje o bloque de thinking | `text` | — | ✅ |
| `beforeTabFileRead` / `afterTabFileEdit` | Autocompletado Tab | `file_path`, `content` / `edits` | allow/deny / — | ❌ |
| `workspaceOpen` | Al abrir el workspace o cambiar carpetas | `workspace_roots`, `user_email` | `pluginPaths` (plugins a cargar) | ❌ |

Desde CLI `v2026.10.28`, un `ask` en `beforeShellExecution` pide confirmación aunque Auto-review hubiera aprobado el comando.

### 4.4 Salida y Códigos de Salida

| Código | Efecto |
| :--- | :--- |
| 0 | Se usa el JSON (`permission`, `user_message`, `agent_message`, `updated_input`…) |
| 2 | deny |
| Otro | Deja pasar, salvo `failClosed: true` |
| JSON inválido en hook de permiso | Bloquea siempre |

Al combinar hooks: **deny > ask > allow**; `user_message` y `agent_message` se concatenan.

### 4.5 Matchers y Directorio de Trabajo

- `preToolUse`/`postToolUse`/`postToolUseFailure`: tipo de tool (`Shell`, `Read`, `Write`, `Grep`, `Delete`, `Task`, `MCP:<tool>`).
- `subagentStart`/`subagentStop`: tipo de subagente.
- Hooks de shell: el comando completo.
- Otros eventos usan un valor fijo (`beforeReadFile` → `Read`, `afterFileEdit` → `Write`, `beforeSubmitPrompt` → `UserPromptSubmit`, `stop` → `Stop`).
- **cwd:** proyecto → raíz del proyecto; usuario → `~/.cursor/`; plugin → carpeta del plugin (salvo `stop`/`subagentStop`, que corren en la raíz del workspace).

### 4.6 Variables de Entorno

`CURSOR_PROJECT_DIR`, `CLAUDE_PROJECT_DIR` (alias), `CURSOR_VERSION`, `CURSOR_USER_EMAIL`, `CURSOR_TRANSCRIPT_PATH`, `CURSOR_CODE_REMOTE`, `CURSOR_PLUGIN_ROOT` (solo hooks de plugin).

### 4.7 Mapeo con Claude Code

| Claude Code | Cursor |
| :--- | :--- |
| `PreToolUse` | `preToolUse` (+ `beforeShellExecution`, `beforeReadFile`, `beforeMCPExecution` en formato nativo) |
| `PostToolUse` | `postToolUse` (+ `postToolUseFailure`, `afterFileEdit`, `afterShellExecution`) |
| `UserPromptSubmit` | `beforeSubmitPrompt` |
| `Stop` / `SubagentStop` | `stop` / `subagentStop` |
| `SessionStart` / `SessionEnd` | `sessionStart` / `sessionEnd` |
| `PreCompact` | `preCompact` (solo observa) |
| `Notification`, `PermissionRequest` | **No soportados** |
| — | `subagentStart`, `workspaceOpen`, `afterAgentResponse`, `afterAgentThought`, hooks de Tab (solo Cursor) |

Nombres de tool en el import: `Bash` → `Shell`, `Edit` → `Write`; `Read`, `Write`, `Grep`, `Task`, `WebFetch`, `WebSearch` igual; **`Glob` no se soporta**. Campos: `permissionDecision` → `permission`, `permissionDecisionReason` → `user_message`, `updatedInput` → `updated_input`. En `Stop`, `decision:"block"` + `reason` funciona como follow-up. Acepta salida anidada (`hookSpecificOutput`) y plana. Los hooks de Claude no se distribuyen por team ni enterprise.

> [!CAUTION]
> **Bug abierto** (CLI 2026.09.26; reconocido por staff el 28 sep 2026, sin fix): los hooks importados de plugins de Claude (`~/.claude/plugins/installed_plugins.json`) reciben `tool_name: "Shell"` en vez de `Bash`, `cwd: ""`, `command` sin el array `args`, `failClosed: false` siempre, y se ignora el `{"decision":"block"}` legacy. **Un hook que filtra por `tool_name == "Bash"` deja pasar todo.** Workarounds de staff: usar `hookSpecificOutput.permissionDecision:"deny"` o `exit 2`, tratar `Shell` como `Bash`, resolver el cwd desde `CURSOR_PROJECT_DIR` o `workspace_roots[0]`, y registrar el hook en `~/.cursor/hooks.json` con `failClosed: true`. [Foro](https://forum.cursor.com/t/cursor-agent-imported-claude-code-plugin-hooks-get-a-cursor-shaped-payload-so-pretooluse-gates-fail-open/173171).

---

## 5. Skills

Fuente: [docs/skills](https://cursor.com/docs/skills).

| Aspecto | Detalle |
| :--- | :--- |
| Ubicaciones | Proyecto: `.cursor/skills/`, `.agents/skills/` (también anidadas: aplican a su subárbol). Usuario: `~/.cursor/skills/`, `~/.agents/skills/`. Compatibilidad: `.claude/skills`, `.codex/skills` y sus versiones en `~`. Escaneo recursivo; precedencia ante duplicados no documentada |
| Frontmatter | `name` (obligatorio, = carpeta), `description` (obligatorio), `paths` (globs; `globs` legacy), `disable-model-invocation`, `icon`, `color`, `metadata`, `user-invocable: false` (oculta del autocompletado, CLI `v2026.07.06`) |
| No documentados | `allowed-tools`, `model`, `license`, `compatibility` |
| Estructura | Carpetas opcionales `scripts/`, `references/`, `assets/` |
| Invocación | Automática por descripción; `/skill-name` para un mensaje; **Custom Mode** de sesión con Alt/Option+Enter |
| Built-ins | `/create-hook`, `/create-skill`, `/create-subagent`, `/create-rule`, `/migrate-to-skills`, `/statusline`, `/loop`, `/review`… |

Bug histórico: el CLI no registraba skills de plugins; staff reactivó el flag el 7 may 2026 y no está claro si quedó resuelto ([foro](https://forum.cursor.com/t/cursor-agent-cli-does-not-register-skills-from-plugins-ide-does-parity-gap/158947)).

---

## 6. Comandos Slash

- Markdown plano en `.cursor/commands/` (solo raíz del workspace, sin subcarpetas) y `~/.cursor/commands/`. Comandos de team desde el dashboard (2.0); gestión en Customize (3.9).
- Desde un plugin: carpeta `commands/` con `.md`, `.mdc`, `.markdown` o `.txt` y frontmatter `name` y `description`.
- **Argumentos:** no hay placeholder documentado (`$ARGUMENTS` no verificado). En el IDE el texto tras el comando se pasa como entrada; en el CLI hubo un [bug](https://forum.cursor.com/t/text-after-custom-commands-in-cursor-cli-is-ignored-cut-off/142428) que lo descartaba.
- **Rumbo:** Cursor migra comandos a skills con `disable-model-invocation: true` (`/migrate-to-skills`).

---

## 7. Subagentes y Modos

Fuente: [docs/subagents](https://cursor.com/docs/subagents).

| Aspecto | Detalle |
| :--- | :--- |
| Ubicaciones | `.cursor/agents/`, `.claude/agents/`, `.codex/agents/` y sus versiones en `~` |
| Frontmatter | `name` (por defecto el nombre del archivo), `description`, `model` (`inherit` por defecto, o id con parámetros entre corchetes), `readonly`, `is_background` |
| Tools | **Sin campo `tools`**: heredan todos los del padre, MCP incluido |
| Built-ins | Explore, Browser |
| Invocación | Automática, `/nombre` o en paralelo |
| Comportamiento | Un nivel de anidamiento extra (2.5); aislamiento opcional en worktree o VM (`/in-cloud`); IDE, CLI y Cloud; si el modelo fijado no puede correr, cae al del padre (`v2026.10.22`) |

Los "Custom Modes" de antes hoy se arman como skills de sesión (`icon`, `color`); no se verificó si los modos legacy siguen existiendo. `readonly` e `is_background` no existen en Claude, y Claude rechaza `model: fast`.

---

## 8. MCP

Fuente: [docs/mcp](https://cursor.com/docs/mcp).

| Aspecto | Detalle |
| :--- | :--- |
| Configuración | `.cursor/mcp.json` (proyecto), `~/.cursor/mcp.json` (global): `{"mcpServers": {...}}` |
| Campos | `type`, `command`, `args`, `env`, `envFile` (stdio), `url`, `headers`, `auth{CLIENT_ID, CLIENT_SECRET?, scopes?}` |
| Transportes | stdio, SSE, Streamable HTTP. En Cloud se recomienda HTTP; SSE y `mcp-remote` no se soportan ahí |
| OAuth | PKCE, RFC 9728, RFC 8707, registro dinámico. Redirect escritorio `http://localhost:8787/callback`; web/Cloud `https://www.cursor.com/agents/mcp/oauth/callback` |
| Interpolación | `${env:NAME}`, `${userHome}`, `${workspaceFolder}`, `${workspaceFolderBasename}`, `${pathSeparator}`, `${/}` |
| Protocolo | tools, prompts, resources, roots, elicitation, MCP Apps (UI interactiva) |
| Desde plugin | `mcp.json` o `mcpServers`. Expande `${CURSOR_PLUGIN_ROOT}` y `${CLAUDE_PLUGIN_ROOT}` en `command`, `args`, `env`, `cwd`. **No expande `${PLUGIN_ROOT}` ni `${PLUGIN_DATA}`.** Secretos vía `${VAR}` de `variables`. Prefijo `plugin-<plugin>-<server>` |
| Deeplink | `cursor://anysphere.cursor-deeplink/mcp/install?name=$NAME&config=$BASE64(JSON)` ([install-links](https://cursor.com/docs/mcp/install-links)) |
| Extensión VS Code | `vscode.cursor.mcp.registerServer({name, server})` / `unregisterServer(name)` ([extension-api](https://cursor.com/docs/extension-api)) |
| CLI | `agent mcp list \| list-tools \| login \| enable \| disable`, `/mcp`, `--approve-mcps`; permisos `Mcp(server:tool)` |
| Admin | Allowlist de MCP por comando, URL o tool; distribución por team (Dashboard → Plugins & MCPs) |

---

## 9. Reglas e Instrucciones

Fuente: [docs/rules](https://cursor.com/docs/rules).

| Tipo | Detalle |
| :--- | :--- |
| Reglas de proyecto | `.cursor/rules/**/*.mdc`. Un `.md` sin frontmatter se ignora en esa carpeta |
| Modos (frontmatter) | Always Apply (`alwaysApply: true`), Apply to Specific Files (`globs`), Apply Intelligently (solo `description`), Manual (sin campos; `@regla`) |
| `AGENTS.md` | Raíz y anidado; lo más específico gana |
| **`CLAUDE.md` / `CLAUDE.local.md`** | Se leen igual que `AGENTS.md` y siempre aplican |
| User Rules | En Customize, no en el filesystem |
| Team Rules | Dashboard, opcionalmente con "Enforce" |
| Desde plugin | Sí, en `rules/` |

---

## 10. Configuración del Plugin y Datos

| Claude Code | Cursor |
| :--- | :--- |
| `userConfig` / secretos | `variables` (JSON Schema). Los valores los fija un **admin en el dashboard** (Plugins → Configure) y se usan como `${VAR}`. No queda claro si un usuario sin team puede fijarlos |
| `${CLAUDE_PLUGIN_DATA}` | **Sin equivalente** (ni `CURSOR_PLUGIN_DATA` ni `${PLUGIN_DATA}`) |
| `bin/` en PATH | Sin equivalente; scripts por ruta |
| Settings por defecto | Sin equivalente; lo más cercano es el `env` que devuelve `sessionStart` |

---

## 11. Mods de Interfaz

| Mod | Estado |
| :--- | :--- |
| Temas, keybindings | Extensiones VS Code (Open VSX) y ajustes del IDE ([themes](https://cursor.com/help/customization/themes)) |
| Statusline | **Solo CLI**: `statusLine: {type: "command", command, padding}` en `cli-config.json`, con datos de tokens en vivo (abr 2026); `/statusline`. Reemplaza el footer nativo ([bug](https://forum.cursor.com/t/cursor-cli-custom-statusline-replaces-native-footer-and-cannot-preserve-auto-review-state/166364)). Schema no publicado. No hay statusline en el IDE. Un plugin no lo aporta |
| Output styles | Sin equivalente; lo más cercano son skills como Custom Mode |
| Notificaciones | `notifications` en `cli-config.json`; no verificado si un plugin puede aportarlas |
| APIs para extensiones | Solo `vscode.cursor.mcp.*` y `vscode.cursor.plugins.registerPath/unregisterPath`. **`vscode.lm.registerTool` no se soporta** ([foro](https://forum.cursor.com/t/support-for-language-model-tools/152071)): para dar un tool al agente, la extensión debe levantar y registrar un servidor MCP |
| Canvases de plugin | Introducidos en 3.9 (detalle no investigado) |

---

## 12. LSP y Tareas en Segundo Plano

- **LSP:** solo vía extensiones VS Code en el IDE; un plugin de Cursor no tiene componente LSP.
- **Segundo plano:** sin equivalente a los monitores de Claude. Alternativas: subagentes `is_background` (escriben en `~/.cursor/subagents/`), Cloud Agents con Automations (cron, webhooks), Agent SDK con `channels/`.

---

## 13. Distribución

| Aspecto | Detalle |
| :--- | :--- |
| Marketplace público | Enviar un repo Git público en cursor.com/marketplace/publish. Revisión manual, también en cada actualización ([plugins](https://cursor.com/help/customization/plugins)) |
| Multi-plugin | `.cursor-plugin/marketplace.json`: `name`, `owner{name}`, `plugins[]` (máx. 10 MB), `metadata.pluginRoot` opcional. El `plugin.json` gana sobre los valores del marketplace |
| Instalación IDE | Customize → Install (scope usuario o proyecto), o `/add-plugin` |
| Instalación CLI | `/plugin`, `agent plugin marketplace add\|list\|update\|remove`, URL git, `--plugin-dir` |
| Desarrollo local | `~/.cursor/plugins/local/<plugin>` + Reload Window |
| Versionado | Solo el campo `version`; sin pin documentado para usuarios |
| Actualizaciones | Marketplaces de team con Auto Refresh vía GitHub App (≤ cada 10 min) o Refresh manual. Cloud Agents conservan la versión con que arrancaron |
| Team / Enterprise | Dashboard → Plugins & MCPs: importa repos de GitHub, GitLab, Bitbucket, Azure DevOps; modos Default Off / Default On / Required; restricción por grupos (SCIM); "Allow Local Plugin Imports" apagado en Enterprise. Plantilla `fieldsphere/cursor-team-marketplace-template`. Hooks MDM en `/etc/cursor/hooks.json` y equivalentes ([docs/plugins](https://cursor.com/docs/plugins)) |

---

## 14. Compatibilidad Directa con Plugins de Claude Code

El setting **"Include Third-Party Plugins, Skills, and Other Configs"** (Settings → Agents → Third-Party Imports, encendido por defecto) importa:

- Hooks de `.claude/settings*.json` y `~/.claude/settings.json`.
- `.claude/skills`, `.claude/agents`, `.claude/commands`.
- `CLAUDE.md` y `CLAUDE.local.md`.
- Hooks, skills y comandos de plugins de Claude instalados (`~/.claude/plugins/installed_plugins.json`). El CLI muestra los plugins importados junto a los nativos desde may 2026.
- MCP: no verificado (un feature request dice que el toggle también lo importa).

**Problemas conocidos:**

- En el CLI el flag está fijo en `true`, sin opt-out ([staff, 29 jul 2026](https://forum.cursor.com/t/no-way-to-opt-out-of-third-party-claude-code-codex-config-loading-the-flag-is-hardcoded/166558)).
- Hooks importados fallan abierto (§4.7).
- Un `CLAUDE.md` symlink a `AGENTS.md` carga las reglas dos veces.
- Subagentes de Claude pueden referenciar tools o modelos que Cursor no tiene.
- Las skills de `~/.claude/skills` inflan los tokens ([foro](https://forum.cursor.com/t/excessive-token-usage-cursor-auto-loads-too-many-skills-from-claude-skills-at-conversation-start/160677)).
- `PreToolUse` sobre `Edit`/`Write` termina en `preToolUse` con matcher `Write`, sin `ask`.

---

## 15. Tabla de Compatibilidad por Componente

| Claude Code | Cursor | ¿Soportado? | Notas |
| :--- | :--- | :---: | :--- |
| `.claude-plugin/plugin.json` | `.cursor-plugin/plugin.json` | Parcial | Campos similares; repo dual recomendado |
| `userConfig` / secretos | `variables` + `${VAR}` | Parcial | Valores fijados por admin |
| `${CLAUDE_PLUGIN_ROOT}` | `${CURSOR_PLUGIN_ROOT}` (+ `${CLAUDE_PLUGIN_ROOT}` en MCP) | Sí | En hooks como variable de entorno |
| `${CLAUDE_PLUGIN_DATA}` | — | No | |
| Skills | `skills/*/SKILL.md` | Sí | Sin `allowed-tools`/`model`; agrega `paths`, `icon`, `color` |
| Comandos | `commands/*.md` | Parcial | Argumentos sin documentar; migración a skills |
| Subagentes | `agents/*.md` | Parcial | Sin `tools`; agrega `readonly`, `is_background` |
| Hook `PreToolUse` | `preToolUse` / `beforeShellExecution` / `beforeReadFile` / `beforeMCPExecution` | Parcial | `ask` solo en shell y MCP |
| Hook `PostToolUse` | `postToolUse` / `postToolUseFailure` / `afterFileEdit` | Sí | |
| Hook `UserPromptSubmit` | `beforeSubmitPrompt` | Sí | `continue:false` bloquea |
| Hooks `Stop` / `SubagentStop` | `stop` / `subagentStop` | Sí | `followup_message`, `loop_limit` |
| Hooks `SessionStart` / `SessionEnd` | `sessionStart` / `sessionEnd` | Sí (no Cloud) | |
| Hook `PreCompact` | `preCompact` | Parcial | Solo observa |
| Hooks `Notification` / `PermissionRequest` | — | No | |
| Hooks tipo prompt | `type: "prompt"` | Sí (no Cloud) | |
| MCP | `mcp.json` / `mcpServers` | Sí | Prefijo `plugin-` |
| LSP | Extensiones VS Code | Parcial | Solo IDE, fuera del plugin |
| Monitores | — | No | Automations / SDK |
| `bin/` en PATH | — | No | |
| Settings por defecto | — | No | Solo `env` de `sessionStart` |
| Output styles | Skill como Custom Mode | Parcial | |
| Temas | Extensiones VS Code | Sí (fuera del plugin) | |
| Statusline | `statusLine` del CLI | Parcial | Solo CLI, no desde plugin |
| Reglas / `CLAUDE.md` | `rules/*.mdc`, `AGENTS.md`, `CLAUDE.md` | Sí | |
| Marketplaces | `.cursor-plugin/marketplace.json` | Sí | Formato propio; revisión manual |
| Scopes | Usuario, proyecto, team, enterprise | Sí | |
| Actualizaciones | Auto Refresh / Refresh / `marketplace update` | Parcial | Sin pin |

---

## 16. No Verificado

- Si Cursor carga un `.claude-plugin/plugin.json` como plugin nativo, sin pasar por el import de `installed_plugins.json`.
- Si se importan `.mcp.json` y el MCP de plugins de Claude.
- Sintaxis de argumentos en comandos.
- Si skills aceptan `allowed-tools`/`model` y subagentes aceptan `tools`.
- Resolución de skills con nombre duplicado.
- Timeout por defecto de los hooks y traducción de `ok` de un hook prompt a allow/deny.
- Matriz completa de eventos de hook por superficie en el CLI.
- Si `cursor-agent` sigue como alias de `agent`.
- Schema oficial de `statusLine`.
- Ubicación en disco de los plugins del marketplace.
- Si un usuario sin team puede fijar `variables`.
- Si el bug de fail-open de los hooks importados se corrigió después del 28 sep 2026.
- Si `preToolUse` con matcher `Write` recibe el contenido completo en `tool_input`.
