# Panorama y Matrices por Componente

> Comparación transversal de la extensibilidad de Claude Code, OpenAI Codex, Google Antigravity, Gemini CLI, OpenCode, Cursor, GitHub Copilot y Herdr, componente por componente: manifiesto, hooks, skills, comandos, subagentes, MCP, reglas, configuración y datos, mods de interfaz, LSP y segundo plano, y distribución. Cada celda resume el detalle de los documentos 02–07. Corte al 2026-11-19.

---

## 1. Respuesta Corta

**No existe un formato de plugin universal.** Hay tres niveles de portabilidad:

| Nivel | Componentes | Situación |
| :--- | :--- | :--- |
| **Portable** | Skills (`SKILL.md` con `name` + `description`), servidores MCP, instrucciones en `AGENTS.md` | Todos los agentes los soportan; solo cambian ubicaciones y el archivo de config de MCP. Es lo que estandariza **Agent Plugins 1.0** |
| **Traducible** | Manifiesto, hooks, subagentes, comandos | Existen en casi todos, con formatos y semánticas distintas. Requieren adaptadores |
| **Específico del host** | `userConfig`, `bin/`, output styles, temas, statusline, LSP, monitores, paneles de UI | Cada agente tiene su versión o no tiene ninguna. Se construyen por separado |

**El formato de Claude Code es el estándar de hecho** para plugins con hooks: Codex, Copilot, Cursor (vía import) y Antigravity (vía `agy plugin import claude`) leen total o parcialmente `.claude-plugin/plugin.json`, `hooks/hooks.json`, `.claude/skills` o `CLAUDE.md`. Pero **leer el formato no garantiza la misma semántica**: varios hosts fallan abierto ante decisiones que Claude respeta (ver §3.3).

**Herdr** es un caso aparte: es un multiplexor que hospeda a los demás agentes. Sus plugins orquestan panes y reaccionan a eventos después del hecho; no interceptan tools.

---

## 2. Manifiesto y Empaquetado

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot | Herdr |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Manifiesto propio | `.claude-plugin/plugin.json` | `.codex-plugin/plugin.json` | `.antigravity-plugin/plugin.json` (o `.gemini-plugin/`, raíz) | `gemini-extension.json` | `package.json` + `Plugin.define({id})` | `.cursor-plugin/plugin.json` | `plugin.json` (1.0 o legado) | `herdr-plugin.toml` |
| **Lee `.claude-plugin/plugin.json`** | ✅ | ✅ (orden: `plugin.json` 1.0 → `.codex-plugin` → `.claude-plugin` → `.cursor-plugin`) | ⚠️ solo vía `agy plugin import`/`install` | ❌ | ❌ | ⚠️ solo vía import automático (con bug) | ✅ (legado) | ❌ |
| Agent Plugins 1.0 (`plugin.json` raíz con `$schema`) | ❌ | ✅ (⚠️ sin hooks en `main`) | ❌ (solo skills por estructura) | ❌ | ❌ | ✅ (solo skills y MCP) | ✅ (schemas 1.0.0 y 1.1.0) | ❌ |
| Rutas de componentes configurables | ✅ | ✅ (legado) | ❌ fijas | ❌ fijas | — | ✅ (reemplazan el descubrimiento) | ✅ legado / ❌ 1.0 | — |
| Campos desconocidos | — | Se ignoran | Warning (error con `--strict`) | — | — | — | Se reportan y se ignoran | — |
| Validación local | `claude plugin validate --strict` | ❌ (portal de envío) | `agy plugin validate [--strict]` | `gemini extensions validate` (no documentado) | ❌ | ❌ (checklist) | ❌ | Al instalar |

---

## 3. Hooks

### 3.1 Modelo

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot | Herdr |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Mecanismo | Subprocess + JSON | Subprocess + JSON | Subprocess + JSON | Subprocess + JSON | **Plugin TS en proceso** | Subprocess + JSON | Subprocess + JSON (+ HTTP) | Subprocess, **sin salida** |
| Archivo en plugin | `hooks/hooks.json` | `hooks/hooks.json` | `hooks.json` (**raíz**, indexado por nombre) | `hooks/hooks.json` (envuelto en `{"hooks":{}}`) | código | `hooks/hooks.json` (o ruta del manifiesto) | `hooks.json` / `hooks/hooks.json` / `com.github.copilot/hooks/` | `[[events]]` en el TOML |
| Nº de eventos | ~12+ | 14 | 6 | 11 | ~15 hooks + bus | 21 | 14 | 22 (posteriores) |
| Timeout por defecto | — | 600 s | 30 s | 60 s (ms) | — | no documentado | 30 s | — |
| Tipos de handler | command, prompt… | command, mcp_tool (prompt/agent se omiten) | command | command | — | command, **prompt** | command, http, prompt (solo sessionStart) | argv |
| Confianza | — | **Revisión por hash en `/hooks`** | Workspace Trust | Huella + folder trust | — | — | Workspace admission (agentes) | Sin sandbox |

### 3.2 Decisiones en el Hook Previo a un Tool

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode V2 | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Bloquear con `exit 2` | ✅ | ✅ (preferir JSON) | ❌ **falla abierto** | ✅ | `throw` | ✅ | ✅ |
| Bloquear con JSON | ✅ | ✅ `deny` | ✅ `decision: "deny"` | ✅ | — | ✅ `permission` | ✅ plano |
| Preguntar (`ask`) | ✅ | ❌ **falla abierto** | ✅ `ask`/`force_ask` | ❌ → allow | ✅ `permission.assert` | ⚠️ solo shell/MCP | ⚠️ (deny en autopilot/nube) |
| `allow` explícito | ✅ | ❌ sin `updatedInput` | ✅ `allow`/`auto_approve` | ✅ | `evaluate` | ✅ | ✅ |
| Reescribir input | ✅ | ✅ | ✅ `overwrite` | ✅ | ✅ | ⚠️ bug en `Write` | ✅ `modifiedArgs` |
| Contexto al modelo | ✅ | ✅ (~2500 tokens) | ❌ (solo `reason`) | ❌ | ❌ | ✅ `agent_message` | ❓ |
| Otro código ≠ 0 | Error no bloqueante | Falla abierto | Falla abierto | Warning | — | Abierto (salvo `failClosed`) | **Cerrado** (solo preToolUse) |
| Timeout | — | Falla abierto | No documentado | SIGTERM | — | — | **Falla abierto** |

### 3.3 Hosts que Fallan Abierto ante un Hook de Claude sin Adaptar

| Host | Causa |
| :--- | :--- |
| Antigravity | `exit 2` se ignora; stdin camelCase (`toolCall.args`) |
| Codex | `ask` y `allow` sin `updatedInput` dejan el hook fallido |
| Gemini CLI | `ask` se ignora |
| Cursor (import) | Payload con `tool_name: "Shell"`, `cwd: ""`, `failClosed: false` |
| Copilot | Timeout; argumentos no remapeados; salida anidada probablemente ignorada |

### 3.4 Mapeo de Eventos

| Claude Code | Codex | Antigravity | Gemini CLI | OpenCode (V2) | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `SessionStart` | `SessionStart` | `SessionStart` | `SessionStart` | evento `session.created` | `sessionStart` | `sessionStart` |
| `UserPromptSubmit` | `UserPromptSubmit` | `PreInvocation` (aprox.) | `BeforeAgent` | `session.hook("prompt")` | `beforeSubmitPrompt` | `userPromptSubmitted` |
| `PreToolUse` | `PreToolUse` | `PreToolUse` | `BeforeTool` | `tool.hook("execute.before")` | `preToolUse` + `beforeShellExecution`/`beforeReadFile`/`beforeMCPExecution` | `preToolUse` |
| `PermissionRequest` | `PermissionRequest` | — | — | `permission.hook("evaluate")` | — | `permissionRequest` |
| `PostToolUse` | `PostToolUse` | `PostToolUse` (sin output) | `AfterTool` | `tool.hook("execute.after")` | `postToolUse` | `postToolUse` |
| `PostToolUseFailure` | (`PostToolUse`) | — | — | — | `postToolUseFailure` | `postToolUseFailure` |
| `Stop` | `Stop` | `Stop` | `AfterAgent` | `session.idle` (observa) | `stop` | `agentStop` |
| `SubagentStart` | `SubagentStart` | — | — | — | `subagentStart` | `subagentStart` |
| `SubagentStop` | `SubagentStop` | — | `AfterAgent` | eventos de la sesión hija | `subagentStop` | `subagentStop` |
| `PreCompact` | `PreCompact` (+ `PostCompact`) | — | `PreCompress` | `session.hook("compaction")` | `preCompact` | `preCompact` |
| `Notification` | — | — | `Notification` | `permission.asked` (observa) | — | `notification` |
| `SessionEnd` | `SessionEnd` | — | `SessionEnd` | `session.deleted` | `sessionEnd` | `sessionEnd` |
| Exclusivos | `PreModelRequest`, `PostModelRequest`, `Interrupt` | `PostInvocation` | `BeforeModel`, `AfterModel`, `BeforeToolSelection` | `context`, `model.request`, `http.*`, `retry`, `shell.hook` | `afterAgentResponse`, `afterAgentThought`, `workspaceOpen`, Tab | `errorOccurred`, `userPromptTransformed` |

### 3.5 Herramientas de Shell, Lectura y Escritura

| | shell | lectura | escritura | edición |
| :--- | :--- | :--- | :--- | :--- |
| Claude Code | `Bash.command` | `Read.file_path` | `Write.file_path/content` | `Edit.file_path/old_string/new_string` |
| Codex | `Bash.command` | — (shell/MCP) | `apply_patch.command` (patch) | `apply_patch.command` (patch) |
| Antigravity | `run_command.CommandLine` | `view_file.AbsolutePath` | `write_to_file.TargetFile/CodeContent` | `replace_file_content.TargetFile/TargetContent/ReplacementContent` |
| Gemini CLI | `run_shell_command.command` | `read_file.file_path` | `write_file.file_path/content` | `replace.file_path/old_string/new_string` |
| OpenCode V2 | `shell.command` | `read.path` | `write.path/content` | `edit.path/oldString/newString`, `patch.patchText` |
| Cursor | `Shell` / `beforeShellExecution.command` | `beforeReadFile.file_path` | `Write` (campos no documentados) | `Write` |
| Copilot (PascalCase) | `Bash` | `Read` (args nativos) | `Write` (args nativos) | `Edit` (incluye `apply_patch`) |

---

## 4. Skills

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Desde plugin | ✅ | ✅ | ✅ | ✅ | ✅ (V2 `ctx.skill.transform`) | ✅ | ✅ |
| Lee `.claude/skills` | ✅ | ❌ (`.agents/skills`) | ❌ | ❌ | ✅ | ✅ | ✅ |
| Lee `.agents/skills` | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Frontmatter respetado | todo | `name`, `description` (+ `agents/openai.yaml`) | `name`, `description`, `disable-model-invocation`, `license` | `name`, `description` | `name`, `description`, `disable-model-invocation` (V2) | `name`, `description`, `paths`, `disable-model-invocation`, `icon`, `color` | `name`, `description`, `allowed-tools` (CLI), `disable-model-invocation` (VS Code) |
| Invocación explícita | `/skill` | `$skill` | `/skill` (`plugin:skill`) | tool con confirmación | tool `skill` / `@id` | `/skill` | `/skill` |

**Conclusión:** solo `name` y `description` funcionan en todos. `allowed-tools` y `model` se ignoran casi en todas partes.

---

## 5. Comandos Slash

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Formato | `commands/*.md` | ❌ (prompts obsoletos) | ❌ (skills) | `commands/*.toml` | `commands/*.md` | `commands/*.md` | `commands/` (no documentado) |
| Desde plugin | ✅ | Se **convierten a skills** con restricciones | El import los convierte a skills | ✅ | ✅ (V2) | ✅ | ✅ |
| Argumentos | `$ARGUMENTS` | — | — | `{{args}}` | `$ARGUMENTS`, `$1` | no documentado | no documentado |
| Tendencia | | Skills | Skills | | | Migrar a skills | Skills |

**Conclusión:** los comandos convergen en skills. Un plugin multi-agente debería expresar sus comandos como skills con `disable-model-invocation`.

---

## 6. Subagentes

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Formato | `agents/*.md` | `.codex/agents/*.toml` | `agents/*.md` | `agents/*.md` | `agents/*.md` | `agents/*.md` | `*.agent.md` |
| Desde plugin | ✅ | ❌ | ✅ | ✅ | ⚠️ (no `add`) | ✅ | ✅ |
| Lee `.claude/agents` | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Lista de tools | ✅ | ❌ | ✅ (nombres propios) | ✅ | permisos | ❌ (hereda) | ✅ (alias) |
| `model` | id | id | tier (`flash`, `pro`) | id | `provider/model` | id o `inherit` | id |

---

## 7. MCP

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Archivo en plugin | `.mcp.json` | `.mcp.json` / `mcp.json` | `mcp_config.json` | `mcpServers` en manifiesto | `ctx.mcp.transform` | `mcp.json` / `mcpServers` | `mcp.json` / `.mcp.json` |
| stdio / HTTP / SSE | ✅ / ✅ / ✅ | ✅ / ✅ / ❓ | ✅ / ✅ / ❌ | ✅ / ✅ / ✅ | ✅ / ✅ / ❓ | ✅ / ✅ / ✅ | ✅ / ✅ / ✅ |
| Raíz del plugin en config | `${CLAUDE_PLUGIN_ROOT}` | ❓ (`cwd` = raíz) | `${PLUGIN_ROOT}` | `${extensionPath}` | `import.meta.url` | `${CURSOR_PLUGIN_ROOT}` y `${CLAUDE_PLUGIN_ROOT}` | `${PLUGIN_ROOT}` y `${CLAUDE_PLUGIN_ROOT}` |
| Secretos | `userConfig` | OAuth / env | `variables` (deshabilita hasta configurar) | `settings[]` (keychain) | `{env:}` | `variables` (admin) | `${input:}` / `COPILOT_MCP_*` |
| OAuth | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (no en nube) |

**Conclusión:** MCP es el componente con mejor portabilidad funcional; la diferencia está en el nombre del archivo de configuración y en cómo se inyectan secretos.

---

## 8. Reglas e Instrucciones

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Archivo principal | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md`, `GEMINI.md` | `GEMINI.md` (configurable) | `AGENTS.md` | `.cursor/rules/*.mdc`, `AGENTS.md` | `copilot-instructions.md`, `AGENTS.md` |
| Lee `CLAUDE.md` | ✅ | ❌ (salvo fallback) | ❌ | ⚠️ con `context.fileName` | ✅ fallback | ✅ | ✅ (CLI) |
| Reglas desde plugin | ✅ | ❌ | ✅ `rules/` con `trigger` obligatorio | `contextFileName` | ❌ (solo código) | ✅ `rules/*.mdc` | ✅ `com.github.copilot/rules/` |

---

## 9. Configuración del Plugin y Datos

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot | Herdr |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `userConfig` (prompt al instalar) | ✅ | ❌ | ⚠️ solo MCP `variables` | ✅ `settings[]` | ❌ (`options`) | ⚠️ `variables` (admin) | ❌ | ❌ |
| Raíz del plugin | `CLAUDE_PLUGIN_ROOT` | `PLUGIN_ROOT` + alias | `PLUGIN_ROOT` (≥ 1.5.3) | `${extensionPath}` | `import.meta.url` | `CURSOR_PLUGIN_ROOT` | `PLUGIN_ROOT` + alias (⚠️ hooks) | `HERDR_PLUGIN_ROOT` |
| Datos persistentes | `CLAUDE_PLUGIN_DATA` | `PLUGIN_DATA` + alias | `PLUGIN_DATA` | ❌ | `ctx.storage` (KV) | ❌ | `PLUGIN_DATA` + alias | `HERDR_PLUGIN_STATE_DIR` |
| Raíz del proyecto en hooks | `CLAUDE_PROJECT_DIR` | ❌ (`cwd`) | `workspacePaths[]`, `cwd` | `GEMINI_PROJECT_DIR` + alias Claude | `ctx.location` | `CURSOR_PROJECT_DIR` + alias Claude | ⚠️ `CLAUDE_PROJECT_DIR` | — |
| `bin/` en PATH | ✅ | ❌ | ⚠️ `bin/` ("por nombre") | ❌ | ⚠️ vía `shell.env` | ❌ | ❌ | ✅ (argv relativo) |
| Settings por defecto | ✅ | ❌ | ❌ | ⚠️ `excludeTools`, policies | ⚠️ transforms | ❌ | ❌ | ❌ |

---

## 10. Mods de Interfaz

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot | Herdr |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Temas desde plugin | ✅ | ❌ (`.tmTheme` usuario) | ❌ | ✅ `themes[]` | ✅ V1 / archivos V2 | ❌ (extensiones VS Code) | ❌ | ❌ (`[theme.custom]` usuario) |
| Statusline | ✅ | ✅ **vía MCP desde plugin** (experimental) | CLI (usuario, stdin casi idéntico a Claude) | ❌ (footer) | ✅ slots TUI | CLI (usuario) | CLI (usuario, experimental) | `tab_bar_right` |
| Output styles | ✅ | ❌ | ❌ | ❌ | ❌ (system del agente) | ❌ (skill como modo) | ❌ | ❌ |
| UI propia (paneles, toasts) | mods | ❌ | Paneles (App, sin documentar) | ❌ | ✅ TUI/GUI plugins | Canvases | Extensiones CLI | `[[panes]]`, mods JS (app) |
| Keybindings | ✅ | usuario | usuario | usuario | usuario / plugin TUI | VS Code | ❌ | `[[keys.command]]` |

---

## 11. LSP y Segundo Plano

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot | Herdr |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| LSP desde plugin | ✅ `.lsp.json` | ❌ | ❌ | ❌ | V1 config / V2 ❌ | ❌ (extensiones) | ✅ `lsp.json` (CLI) | ❌ |
| Monitores / procesos | ✅ `monitors.json` | ❌ | ✅ **sidecars** (App) | ❌ | Procesos en `setup` | ❌ | ❌ | `[[startup]]` |

---

## 12. Distribución

| | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot | Herdr |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Marketplace | `marketplace.json` | `.agents/plugins/` o **`.claude-plugin/marketplace.json`** | Solo oficial, curado | Galería por topic GitHub | npm/Git (sin oficial) | `.cursor-plugin/marketplace.json`, revisión manual | **`.claude-plugin/marketplace.json`**, awesome-copilot | Topic GitHub, sin revisión |
| Fuente `github` de Claude | ✅ | ❌ (entrada omitida) | — | — | — | — | ✅ | — |
| CLI | `claude plugin` | `codex plugin` | `agy plugin` | `gemini extensions` | `opencode plugin` | `agent plugin` | `copilot plugin` | `herdr plugin` |
| Auto-update | ✅ | ✅ (0.171) | Marketplace | `--auto-update` | `@latest` cada 24 h | Team Auto Refresh | Opt-in | `update` manual |
| Gestión enterprise | managed settings | `requirements.toml`, MDM | Consola Gemini Enterprise | admin controls | `/etc/opencode`, MDM, políticas | Dashboard, MDM | managed-settings en `.github-private` | ❌ |

---

## 13. Hallazgos que Cambian el Diseño

- **Agent Plugins 1.0** (agent-plugins.org, 2026-08-06; AWS, Cursor, Microsoft, OpenAI, Vercel, Google; sin Anthropic) estandariza solo skills y MCP. Cuidado: en Codex un `plugin.json` raíz con su `$schema` tiene prioridad sobre `.claude-plugin/` y, en `main`, no carga hooks.
- **Los comandos convergen en skills** (Antigravity, Codex, Cursor, Copilot).
- **`apply_patch` es el formato de edición dominante fuera de Claude** (Codex, OpenCode con GPT, Copilot, Factory).
- **Gemini CLI se integró en Antigravity** (mayo 2026); queda como destino de pago.
- **OpenCode 2.x rompió la API de plugins** de 1.x.
- **Codex y Copilot convierten o leen plugins de Claude**; Antigravity tiene `agy plugin import claude`; Cursor los importa automáticamente sin opt-out en el CLI.
- **Los multiplexores (Herdr) abren vías de ejecución indirecta** que ningún hook de agente ve.

- **Los mods de Claude Code (TypeScript en proceso) no tienen equivalente salvo OpenCode**: reemplazar la compactación, dibujar UI interactiva o leer telemetría interna solo es posible ahí. OpenCode es además el único host con runtime embebido (Bun) para correr lógica sin instalar nada.

Guía práctica: [`08-guia-plugin-multi-agente.md`](./08-guia-plugin-multi-agente.md). Casos reales: [`09-caso-de-estudio-synapse.md`](./09-caso-de-estudio-synapse.md) (hooks por subprocess) y [`10-caso-de-estudio-session-handoff.md`](./10-caso-de-estudio-session-handoff.md) (mod en proceso).
