# GitHub Copilot

> Superficie completa de extensibilidad de GitHub Copilot (CLI `1.0.114`, Copilot app, VS Code con sus harnesses, cloud agent y JetBrains): plugins (Agent Plugins 1.0 y legado), 14 eventos de hook, skills, prompts y comandos, agentes personalizados, MCP, instrucciones, LSP, extensiones JS del CLI, statusline, temas, marketplaces y políticas de organización. Corte al 2026-11-19.

---

## 1. Visión General y Superficies

| Superficie | Estado (nov 2026) | Qué soporta |
| :--- | :--- | :--- |
| **Copilot CLI** | 1.0.114 (2026-11-18) | Lo más completo: plugins en dos formatos, 14 eventos de hook, skills, agentes, comandos, reglas, MCP, LSP, extensiones JS (experimentales), statusline, temas |
| **Copilot app** | Comparte el runtime del CLI | Plugins, extensiones (incluye "canvases") |
| **VS Code — harness Copilot** | Hooks en preview | Mismo runtime y formato de hooks que el CLI; plugins, skills, agentes, MCP (`~/.copilot/mcp-config.json`) |
| **VS Code — harness Local** | **Obsoleto, se retira en febrero 2027** | Prompt files, chat participants, `.vscode/mcp.json`, hooks en formato Claude |
| **VS Code — harness Claude / Codex** | Disponibles (se desactivan con managed settings enterprise) | El harness Claude corre sobre Claude Agent SDK con la config `.claude` del proyecto |
| **Cloud agent** | Planes pagos | `.github/hooks`, agentes, skills, MCP del repo, plugins vía `enabledPlugins` |
| **JetBrains** | Agentes y subagentes GA (mar 2026); skills, hooks y prompt files GA (jun 2026); skills y agentes de org (sep 2026) | Sin evidencia de soporte de plugins |

Fuentes: [agent-harnesses](https://code.visualstudio.com/docs/agents/concepts/agent-harnesses), [hooks VS Code](https://code.visualstudio.com/docs/copilot/customization/hooks), [JetBrains mar](https://github.blog/changelog/2026-03-11-major-agentic-capabilities-improvements-in-github-copilot-for-jetbrains-ides/), [jun](https://github.blog/changelog/2026-06-02-introducing-copilot-cli-and-agentic-capabilities-enhancements-in-jetbrains-ides/), [sep](https://github.blog/changelog/2026-09-22-new-features-and-improvements-in-copilot-for-jetbrains).

---

## 2. Modelo de Extensión

| Mecanismo | Estado | Notas |
| :--- | :--- | :--- |
| **Plugins** (CLI, app, VS Code, nube) | GA | Dos formatos (§3) |
| **Agent Plugins 1.0** ([agent-plugins.org](https://agent-plugins.org/)) | Estándar abierto publicado el 2026-08-06; mantenido por AWS, Anysphere (Cursor), Microsoft, OpenAI, Vercel y Google (Anthropic no figura) | Solo estandariza `skills/` y `mcp.json`; lo propio del cliente va en un directorio reverse-domain (`com.github.copilot/`). GA en VS Code, CLI, SDK y app ([changelog](https://github.blog/changelog/2026-08-12-agent-plugins-1-0-in-vs-code-copilot-cli-and-the-copilot-app)). El CLI acepta schemas 1.0.0 y 1.1.0 |
| **Extensiones VS Code** | GA | Language Model Tools API (tools invocadas en modo agente), Language Model API, contribución `chatSkills`. **Chat participants obsoletos y no soportados en el harness Copilot** ([ai-extensibility-overview](https://code.visualstudio.com/api/extension-guides/ai/ai-extensibility-overview)) |
| **Copilot Extensions (GitHub Apps)** | **Retiradas el 2025-11-10** (reemplazo: MCP) ([sunset](https://github.blog/changelog/2025-09-24-deprecate-github-copilot-extensions-github-apps/)) | |
| **Extensiones del CLI** | Experimental | Módulos Node en proceso aparte que aportan tools, slash commands y suscripción a eventos (§11) |
| **Customizations del repo** | GA | `.github/{hooks,agents,skills,instructions,prompts,extensions}`, `.github/copilot/settings(.local).json`, `.github/lsp.json` |

### 2.1 Precedencia

- **Agentes:** el primero encontrado gana: `~/.copilot/agents` → `.github/agents` → `.github/agents` del padre → `.claude/agents` → `.claude/agents` del padre → `--add-dir` → plugins → org/enterprise.
- **Skills:** el primero gana; proyecto antes que personal, plugins al final. Las skills tienen prioridad sobre los comandos.
- **MCP:** gana el último cargado (usuario < plugin < `--additional-mcp-config`).
- **Los plugins no pueden pisar la configuración de proyecto ni la personal.**
- **Settings:** defaults < managed < usuario < repo < local < env < flags; las entradas managed de `enabledPlugins` siempre ganan.

Fuentes: [cli-plugin-reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference), [cli-config-dir-reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference).

---

## 3. Manifiesto

### 3.1 Agent Plugins 1.0

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "my-dev-tools",
  "version": "1.2.0",
  "description": "...",
  "author": { "name": "Jane Doe" },
  "license": "MIT",
  "keywords": ["react"]
}
```

```text
my-dev-tools/
├── plugin.json
├── skills/<x>/SKILL.md          <-- portable
├── mcp.json                     <-- portable (con su propio $schema)
└── com.github.copilot/          <-- específico de Copilot (otros clientes lo ignoran)
    ├── agents/
    ├── commands/
    ├── rules/
    ├── hooks/hooks.json
    ├── lsp.json
    └── automations/*.automation.md   (VS Code)
```

- Obligatorios: `$schema` (exacto, 1.0.0 o 1.1.0) y `name` (1–64; minúsculas, dígitos, `-`, `.`; sin `--` ni `..`). Opcionales: `version`, `description`, `author`, `homepage`, `repository`, `license` (SPDX), `keywords`, `extensions`.
- **No se permiten campos de rutas de componentes** (layout fijo). Schema cerrado: campos desconocidos se reportan y se ignoran. **Si el manifiesto se rechaza, el plugin no aporta nada.**

### 3.2 Formato Legado

Se usa cuando no hay `$schema` reconocido. Búsqueda: `.plugin/plugin.json` → `plugin.json` → `.github/plugin/plugin.json` → **`.claude-plugin/plugin.json`**.

| Campo | Detalle |
| :--- | :--- |
| `name` | Obligatorio, kebab-case, máx. 64 |
| Metadatos | `description` (máx. 1024), `version`, `author`, `homepage`, `repository`, `license`, `keywords`, `tags`, `category` |
| Rutas | `agents` (`agents/`), `skills` (`skills/`), `commands`, `hooks` (ruta u objeto inline; `hooks.json` o `hooks/hooks.json`), `mcpServers`, `lspServers`, `extensions` (`{paths, exclusive}`) |
| `userConfig` | **No existe** |

VS Code reconoce cuatro formatos: Agent Plugins, Copilot (`plugin.json`), **Claude (`.claude-plugin/plugin.json`)** y OpenPlugin legado (`.plugin/`) ([agent-plugin-reference](https://code.visualstudio.com/docs/agents/reference/agent-plugin-reference)). `copilot plugin install` reporta los campos ignorados (1.0.106); no hay comando `validate` documentado.

---

## 4. Hooks

Fuentes: [hooks-configuration](https://docs.github.com/en/copilot/reference/hooks-configuration), [cli-hooks-reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference).

### 4.1 Configuración

```json
{
  "version": 1,
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "bash": "python3 ./hooks/guard.py", "timeoutSec": 30 }
    ]
  },
  "disableAllHooks": false
}
```

| Tipo | Campos |
| :--- | :--- |
| `command` | `bash`, `powershell` o `command`; o `exec` + `args` sin shell (solo CLI). `cwd`, `env`, `timeoutSec` (30; alias `timeout`) |
| `http` | `url` (https obligatorio en `preToolUse`/`permissionRequest`), `headers`, `allowedEnvVars`; localhost con `COPILOT_HOOK_ALLOW_LOCALHOST=1` |
| `prompt` | Solo en `sessionStart` |

`matcher`: regex anclada `^(?:P)$`.

**Ubicaciones (CLI, se combinan todas):** policy (`/etc/github-copilot/policy.d/*.json`, `C:\ProgramData\GitHub\Copilot\policy.d`, `HKLM\Software\Policies\GitHub\Copilot`); repo (`.github/hooks/*.json`, `.github/copilot/settings(.local).json`, **`.claude/settings(.local).json`**); usuario (`~/.copilot/hooks/*.json`, `~/.copilot/settings.json`); plugin; frontmatter de agente.

### 4.2 Eventos

| Evento | Momento | Payload principal | Salida | Matcher |
| :--- | :--- | :--- | :--- | :--- |
| `sessionStart` | Sesión nueva o reanudada | `source` (startup/resume/new), `initialPrompt` | `additionalContext` (se acumula) | — |
| `sessionEnd` | Fin de sesión | `reason` (complete/error/abort/timeout/user_exit) | — | — |
| `userPromptSubmitted` | Al enviar un prompt | prompt | `modifiedPrompt` solo en hooks del SDK | — |
| `userPromptTransformed` | Tras la transformación del runtime | — | `modifiedTransformedPrompt` | — |
| `preToolUse` | Antes de cada tool | `toolName`/`toolArgs` o `tool_name`/`tool_input` | `permissionDecision` allow/deny/ask, `permissionDecisionReason`, `modifiedArgs` | toolName |
| `permissionRequest` (solo CLI) | Antes del servicio de permisos | — | `behavior` allow/deny, `message`, `interrupt` | toolName |
| `postToolUse` | Tool exitosa | `toolResult{resultType, textResultForLlm}` | `modifiedResult`, `additionalContext` (máx. 10 KB) | toolName |
| `postToolUseFailure` | Tool fallida | `error` | `additionalContext` | toolName |
| `agentStop` | Fin de turno | `transcriptPath`, `stopReason`, `stop_hook_active` | `decision` block/allow + `reason` (máx. 8 bloqueos seguidos) | — |
| `subagentStart` | Antes de un subagente | `agentName` | `additionalContext` | agentName |
| `subagentStop` | Fin de subagente | `response`, `agentName`, `agentId` | `decision`, `reason`, `modifiedResponse` | — |
| `preCompact` | Antes de compactar | `trigger` manual/auto, `customInstructions` | Solo notificación | trigger |
| `errorOccurred` | Error | `error`, `errorContext`, `recoverable` | — | — |
| `notification` (solo CLI, async) | Notificación del sistema | `message`, `title`, `notification_type` (`shell_completed`, `agent_idle`…) | `additionalContext` | notification_type |

### 4.3 Dos Formatos de Payload

| Nombre del evento | Payload |
| :--- | :--- |
| camelCase (`preToolUse`) | `sessionId`, timestamp en ms, `toolName`, `toolArgs` (puede llegar como string JSON) |
| **PascalCase (`PreToolUse`)** — "VS Code compatible" | `hook_event_name`, `session_id`, timestamp ISO, `cwd`, `tool_name`, `tool_input`, **nombres de tool Claude** |

Mapeo de tools (solo PascalCase): `bash`/`powershell` → `Bash`, `view` → `Read`, `create` → `Write`, `edit`/`str_replace_editor`/`apply_patch` → `Edit`, `grep`/`rg` → `Grep`, `glob` → `Glob`, `web_fetch` → `WebFetch`, `web_search` → `WebSearch`, `ask_user` → `AskUserQuestion`, `update_todo` → `TodoWrite`, `task` → `Agent` (acepta `Task`).

> [!WARNING]
> **Solo se remapea el nombre del tool, no los argumentos.** Los nombres nativos probables son `path`, `file_text`, `old_str`/`new_str` (no confirmado). Un hook escrito para Claude que lee `file_path`/`content`/`new_string` puede recibir campos vacíos. `apply_patch` se mapea a `Edit` pero su input es un patch.

### 4.4 Salida y Códigos de Salida

Salida **plana**: `{"permissionDecision": "deny", "permissionDecisionReason": "...", "modifiedArgs": {...}}`. **`hookSpecificOutput` y `additionalContext` en `preToolUse` no están documentados.**

| Situación | Efecto |
| :--- | :--- |
| `exit 0` | Se parsea el JSON de stdout |
| `exit 2` | **deny** en `preToolUse`/`permissionRequest`; advertencia en el resto (en `postToolUseFailure`, stdout pasa a `additionalContext`) |
| Otro código ≠ 0 | **Falla cerrado solo en `preToolUse`**; abierto en el resto |
| **Timeout** | **Falla abierto siempre** |
| Hook HTTP con error de red o no-2xx en `preToolUse` | Falla abierto |
| Salida | Tope 10 MiB; líneas `{"type": "progress"}` se descartan |

### 4.5 Diferencias por Superficie

| Superficie | Diferencias |
| :--- | :--- |
| VS Code (harness Copilot) | 9 eventos documentados (`sessionStart`, `userPromptSubmitted`, `preToolUse`, `postToolUse`, `preCompact`, `subagentStart`/`subagentStop`, `agentStop`, `sessionEnd`); `{}` = sin decisión; `ask` requiere aprobación interactiva |
| VS Code (harness Local, obsoleto) | **Salida compatible con Claude** (`hookSpecificOutput.permissionDecision`, `updatedInput`, `additionalContext`), `exit 2` bloquea; **ignora matchers**; requiere `chat.useHooks` y `chat.useClaudeHooks` |
| Cloud agent | Solo `bash`/`command`; **`ask` = deny**; sin `notification` ni `permissionRequest`; `preCompact` solo auto. Variables: `GITHUB_COPILOT_API_TOKEN`, `GITHUB_COPILOT_GIT_TOKEN`, `COPILOT_AGENT_PROMPT`, `HOME=/root` (sin `GITHUB_TOKEN`) |
| JetBrains (preview) | `userPromptSubmitted`, `preToolUse`, `postToolUse`, `errorOccurred` |
| Autopilot (CLI) | `ask` = deny |
| Hooks de agente | Requieren "workspace admission"; no corren desde agentes remotos ni de org; un `Stop` en subagente se trata como `SubagentStop` |

### 4.6 Mapeo con Claude Code

| Claude Code | Copilot |
| :--- | :--- |
| `SessionStart` | `sessionStart` |
| `UserPromptSubmit` | `userPromptSubmitted` / `userPromptTransformed` |
| `PreToolUse` | `preToolUse` |
| `PermissionRequest` | `permissionRequest` (solo CLI) |
| `PostToolUse` / `PostToolUseFailure` | `postToolUse` / `postToolUseFailure` |
| `Stop` | `agentStop` |
| `SubagentStart` / `SubagentStop` | `subagentStart` / `subagentStop` |
| `PreCompact` | `preCompact` |
| `Notification` | `notification` (solo CLI) |
| `SessionEnd` | `sessionEnd` |
| — | `errorOccurred` |

---

## 5. Skills

| Aspecto | Detalle |
| :--- | :--- |
| Ubicaciones | Proyecto: `.github/skills`, `.claude/skills`, `.agents/skills`. Personal: `~/.copilot/skills`, `~/.agents/skills`. Nube: `.copilot/skills` del repo de dotfiles. También plugin, `COPILOT_SKILLS_DIRS`, y en VS Code `chatSkills` o `~/.claude/skills` (harness Claude y Local) |
| Frontmatter | `name` (= directorio, máx. 64) y `description` (máx. 1024) obligatorios. CLI: `license`, **`allowed-tools`** (tools pre-aprobadas). VS Code: `argument-hint`, `user-invocable`, `disable-model-invocation`, `context: fork` (experimental). `model` no documentado |
| Invocación | Automática por descripción (carga en 3 niveles); `/nombre` (en VS Code las de plugin como `/plugin:skill`); CLI `/skills list\|info\|add\|reload\|remove`, `copilot skill add\|list\|enable\|disable` |
| Superficies | CLI, app, nube, code review, VS Code, Visual Studio, JetBrains |

Fuentes: [about-agent-skills](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills), [add-skills](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills), [VS Code agent-skills](https://code.visualstudio.com/docs/copilot/customization/agent-skills).

---

## 6. Prompts y Comandos Slash

- **`.github/prompts/*.prompt.md`** corre solo en el harness Local (obsoleto); la recomendación oficial es convertirlos en skills. Frontmatter `description`, `name`, `argument-hint`, `agent` (ask/agent/plan/custom), `model`, `tools` (no existe `mode`). Variables `${input:x[:placeholder]}`, `${selection}`, `#file:`, `#tool:`; `$ARGUMENTS` no documentado ([prompt-files](https://code.visualstudio.com/docs/copilot/customization/prompt-files)). GA en JetBrains.
- **Comandos de plugin:** `com.github.copilot/commands/` (1.0) o campo `commands` (legado). El CLI también lee `.claude/commands/`. **Formato, frontmatter y argumentos no documentados.**
- **Comandos con código:** desde una extensión del CLI con `joinSession({commands: [{name, description, handler(ctx.args)}]})`.

---

## 7. Agentes Personalizados

| Aspecto | Detalle |
| :--- | :--- |
| Archivos | `.github/agents/*.agent.md` (o `.md`), `~/.copilot/agents`, `.claude/agents`, `~/.claude/agents` (VS Code). Org/enterprise: repo `.github-private`. **`.chatmode.md` es legado** (renombrar a `.agent.md`) |
| Frontmatter (GitHub) | `name`, `description` (obligatorio), `target` (`vscode` \| `github-copilot`), `tools`, `model`, `disable-model-invocation`, `user-invocable`; `mcp-servers` y `metadata` (nube y CLI); `infer` retirado; prompt ≤ 30 000 caracteres ([custom-agents-configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration)) |
| Alias de tools | `execute` (shell/Bash/powershell), `read` (Read), `edit` (Edit/Write/MultiEdit), `search` (Grep/Glob), `agent` (Task), `web` (WebFetch/WebSearch), `todo` (TodoWrite); MCP `server/tool` o `server/*` |
| VS Code agrega | `argument-hint`, `agents` (subagentes), `model` como lista, `handoffs[{label, agent, prompt, send, model}]`, `hooks`. Los de `.claude/agents` se leen en formato Claude (`name`, `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `skills`, `mcpServers`, `hooks`, `memory`) ([custom-agent-file-reference](https://code.visualstudio.com/docs/agents/reference/custom-agent-file-reference)) |
| Nube | Ignora `argument-hint` y `handoffs`; versionado por SHA del archivo |

---

## 8. MCP

| Aspecto | Detalle |
| :--- | :--- |
| Ubicaciones | CLI y harness Copilot: `~/.copilot/mcp-config.json` (`mcpServers`), `.mcp.json` en la raíz. Harness Local: `.vscode/mcp.json` y del perfil (`servers`, `inputs`, `sandbox`, `dev`). Plugin: `mcp.json` (1.0) o `.mcp.json` / `.github/mcp.json` / `mcpServers` (legado) |
| Campos portables | `type` (stdio/http/sse/streamable-http), `command`, `args`, `env`, `cwd`, `url`, `headers`, `tools`, `oauthClientId` |
| Variables | Portable `${NAME}`, `${NAME:-default}`; editor `${input:id}` (guardado seguro), `${env:NAME}`; en plugins `${PLUGIN_ROOT}`, `${PLUGIN_DATA}` y `${CLAUDE_PLUGIN_ROOT}` (formato Claude; expandido en command/args/cwd/env/envFile/url/headers) |
| OAuth | VS Code completo (`oauth.clientId`, secret storage). **El cloud agent no soporta remotos con OAuth** |
| Nube | JSON en Settings del repo (`type` y `tools` obligatorios); secretos con prefijo `COPILOT_MCP_`; GitHub MCP (lectura del repo) y Playwright activos por defecto ([cloud-agent MCP](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/extend-cloud-agent-with-mcp)) |
| Política | `allowedMcpServers` (se intersectan), `deniedMcpServers` (se unen, siempre gana); VS Code `chat.mcp.access`. `copilot plugin install` no instala MCP desde registro (va por `/mcp` o `copilot mcp add`); los MCP de un plugin sí se cargan |

---

## 9. Instrucciones

| Tipo | Detalle |
| :--- | :--- |
| Archivos | `.github/copilot-instructions.md`; `.github/instructions/**/*.instructions.md` con `applyTo` (`description` en Local); `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` (`REVIEW.md` en code review) |
| CLI | Todos los anteriores + `~/.copilot/copilot-instructions.md`, `~/.copilot/instructions/**`; desde 1.0.108 `.claude/rules` |
| VS Code Chat | Solo `AGENTS.md` como archivo de agente; el harness Claude lee `CLAUDE.md` y `.claude/rules/*.md` (con `paths`) |
| Organización | GitHub.com, nube, code review |
| Precedencia | No definida ("combination rules differ by harness") |
| Desde plugin | `com.github.copilot/rules/`; frontmatter `alwaysApply` y `globs` según changelog (con `alwaysApply: false` queda fuera hasta habilitarla en `/instructions`); formato completo no documentado |

Fuentes: [custom-instructions-support](https://docs.github.com/en/copilot/reference/custom-instructions-support), [VS Code custom-instructions](https://code.visualstudio.com/docs/copilot/customization/custom-instructions).

---

## 10. Configuración del Plugin y Datos

| Claude Code | Copilot |
| :--- | :--- |
| `userConfig` / secretos | **No existe.** Solo inputs de registro MCP, `${input:}` del editor y prompts de variables de entorno de extensiones del CLI (1.0.112) |
| `${CLAUDE_PLUGIN_DATA}` | `PLUGIN_DATA` (alias `COPILOT_PLUGIN_DATA`, `CLAUDE_PLUGIN_DATA`) en `~/.copilot/plugin-data/`, sobrevive a updates |
| `${CLAUDE_PLUGIN_ROOT}` | `PLUGIN_ROOT` (alias `CLAUDE_PLUGIN_ROOT`, `COPILOT_PLUGIN_ROOT`) en MCP, LSP y agentes; en hooks del CLI se expande `${COPILOT_PLUGIN_ROOT}` en `cwd` y `env` (1.0.111). **Como variable de entorno del proceso del hook: no verificado** |
| Instalación | `~/.copilot/installed-plugins/<marketplace>/<plugin>` o `_direct/` |
| `bin/` en PATH | No existe |
| Settings por defecto | No desde plugin; lo más cercano es `.github/copilot/settings.json` del repo |
| Otras variables | `COPILOT_SESSION_CWD`, `COPILOT_CACHE_HOME`, `COPILOT_AUTO_UPDATE=false`, `COPILOT_HOME` |

---

## 11. Mods de Interfaz

| Mod | Detalle |
| :--- | :--- |
| Temas (CLI) | `/theme` o `theme` (`default`, `github`, `dim`, `high-contrast`, `colorblind`); un plugin no los aporta (no verificado) |
| Statusline (CLI, experimental) | `/statusline` o `statusLine: {type: "command", command, padding?, refreshInterval?}`; el script recibe JSON de la sesión por stdin ([config-dir-reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference)) |
| Otras (CLI) | `footer.showBranch`, `footer.showQuota`, `sidebar.*`, `beep`, `beepOnSchedule` |
| Keybindings, output styles | No documentados |
| **Extensiones del CLI** (experimental) | `.github/extensions/NAME/`, `~/.copilot/extensions/NAME/` o plugin, con `extension.mjs\|cjs\|js`. `import { joinSession } from "@github/copilot-sdk/extension"`: tools (`name`, `description`, `parameters`, `handler`, `defer`, `skipPermission`), commands, `session.on("tool.execution_start" \| "tool.execution_complete" \| "assistant.usage")`, `session.log()`. Requieren `--experimental` o `/experimental on`; `/extensions` (Load & Augment, Load Only, apagado); sandbox opcional (1.0.106); `exclusive: true` suprime las built-in ([create-an-extension](https://docs.github.com/en/copilot/tutorials/create-an-extension)) |
| VS Code | Temas y keybindings por extensiones normales; la UI del chat no se extiende con plugins |

---

## 12. LSP y Segundo Plano

- **LSP (solo CLI):** `~/.copilot/lsp-config.json`, `.github/lsp.json`, en plugin `lsp.json` o `com.github.copilot/lsp.json`. Campos `command|bash|powershell`, `args`, `cwd` (`${PLUGIN_ROOT}`), `env`, `fileExtensions` (obligatorio), `rootUri`, `initializationOptions`. `/lsp` (start/stop/reload/test), `copilot lsp list` ([add-lsp-servers](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/add-lsp-servers)). En VS Code, LSP por extensiones.
- **Segundo plano:** `/tasks`, `Ctrl+X b`, shells detached; el hook `notification` emite `shell_completed` y `agent_idle`. Sin monitores declarativos de plugin.

---

## 13. Distribución

| Aspecto | Detalle |
| :--- | :--- |
| Instalación | `copilot plugin install plugin@marketplace \| OWNER/REPO[:path] \| git-url \| ./local`, `uninstall`, `list --json`, `update [--all]`, `enable`, `disable`; `copilot plugin marketplace add\|list\|browse\|update\|remove`; `/plugin`; `--plugin-dir` (solo lectura) |
| `marketplace.json` | `marketplace.json`, `.plugin/`, `.github/plugin/`, **`.claude-plugin/marketplace.json`**. Campos `name`, `owner{name, email?}`, `plugins[]`, `metadata{description, version, pluginRoot}`; entrada con `name`, `source` (ruta, `{source: "github", repo, ref, path, sha}` o `url`), rutas de componentes, `strict` |
| VS Code | `chat.plugins.marketplaces`, `chat.pluginLocations`; fuentes `owner/repo#ref`, https, `git@`, `file:///`, npm, PyPI |
| Marketplace por defecto | awesome-copilot (`github/copilot-plugins` ya no viene por defecto) |
| Actualizaciones | First-party al iniciar sesión; marketplaces propios solo con `autoUpdate: true` en `extraKnownMarketplaces`; locales con `/restart` |
| Políticas | `copilot/managed-settings.json` en el repo `.github-private` de la org, por MDM o archivo; equipos con `copilot/teams/*.json` y `team-mappings.json`. Claves: `enabledPlugins`, `extraKnownMarketplaces`, `strictKnownMarketplaces` (vacío bloquea todo), `allowedMcpServers`/`deniedMcpServers`; VS Code `allowManagedHooksOnly`; hooks admin en `policy.d` ([enterprise plugin standards](https://docs.github.com/en/copilot/how-tos/administer-copilot/manage-for-enterprise/manage-agents/configure-enterprise-plugin-standards)) |

---

## 14. Compatibilidad Directa con Plugins de Claude Code

| Superficie | Qué lee de Claude |
| :--- | :--- |
| CLI | `.claude-plugin/plugin.json` y `marketplace.json`; `.claude/settings(.local).json` del repo (hooks, `enabledPlugins`, `extraKnownMarketplaces`); `.claude/skills`, `.claude/agents`, `.claude/commands`, `CLAUDE.md`, `.claude/rules`. **No lee `~/.claude/settings.json`** |
| VS Code harness Copilot | Plugins formato Claude con `CLAUDE_PLUGIN_ROOT` y `hooks/hooks.json` |
| VS Code harness Claude | Claude Agent SDK con la config `.claude` del proyecto; lee `~/.claude/settings.json` y `.claude/settings*.json` (requiere `chat.useClaudeHooks`) |

**Brechas:** salida de hooks plana; argumentos de tools sin remapear; sin `userConfig`, `bin/`, output styles ni monitores; formato de `commands/` no documentado; un timeout falla abierto; `ask` = deny en autopilot y nube.

---

## 15. Tabla de Compatibilidad por Componente

| Claude Code | Copilot | ¿Soportado? | Notas |
| :--- | :--- | :---: | :--- |
| `.claude-plugin/plugin.json` | Manifiesto legado / formato Claude | Sí | CLI y VS Code; sin `userConfig` |
| Skills | Agent Skills | Sí | `allowed-tools` (CLI); `disable-model-invocation`/`user-invocable` (VS Code) |
| Comandos | `commands` / prompt files | Parcial | Formato no documentado; prompt files solo en Local |
| Subagentes | `*.agent.md` | Sí | Lee `.claude/agents` |
| Hooks | `hooks.json` v1 | Sí | 14 eventos (CLI); salida plana |
| MCP | `mcp.json` / `.mcp.json` | Sí | Sin OAuth en la nube |
| LSP | `lsp.json` / `lspServers` | Sí (solo CLI) | |
| Monitores | — | No | `/tasks` |
| `bin/` en PATH | — | No | |
| Settings por defecto | `.github/copilot/settings.json` | Parcial | No desde plugin |
| Output styles | — | No | |
| Temas | `theme` | Parcial | Solo built-in |
| Statusline | `statusLine` command | Sí (CLI, experimental) | No desde plugin |
| `userConfig` / secretos | — | No | |
| `${CLAUDE_PLUGIN_ROOT}` | `${PLUGIN_ROOT}` + alias | Parcial | En hooks del CLI solo `${COPILOT_PLUGIN_ROOT}` en cwd/env |
| `${CLAUDE_PLUGIN_DATA}` | `PLUGIN_DATA` + alias | Sí | |
| Marketplaces | `marketplace.json` | Sí | Lee `.claude-plugin/marketplace.json` |
| Instalación / updates | `copilot plugin`, `/plugin` | Sí | `ref`/`sha`; auto-update opt-in |
| Scopes | Usuario, repo, managed, equipo | Sí | Managed gana |
| (exclusivo) | Extensiones JS del CLI, reglas, automations (VS Code) | — | |

---

## 16. No Verificado

- Si los hooks de plugins corren en el cloud agent (las fuentes se contradicen).
- `CLAUDE_PLUGIN_ROOT`, `PLUGIN_ROOT` y `CLAUDE_PROJECT_DIR` como variables de entorno de hooks del CLI (un [issue](https://github.com/github/copilot-cli/issues/4001) reporta `CLAUDE_PROJECT_DIR` ausente en algunos casos).
- Argumentos reales de `view`/`create`/`edit`.
- Formato de comandos de plugin y namespacing en el CLI.
- Formato completo de reglas de plugin.
- Contenido del schema 1.1.0 de Agent Plugins.
- Soporte de plugins en JetBrains.
- Comando oficial de validación.
- Keybindings y output styles en el CLI.
- Rutas de managed settings por MDM por sistema operativo.
- Si `chat.useClaudeHooks` afecta al harness Copilot.
- API de UI (vistas, canvases) de las extensiones del CLI.
