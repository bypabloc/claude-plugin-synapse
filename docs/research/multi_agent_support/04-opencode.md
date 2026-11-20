# OpenCode

> Superficie completa de extensibilidad de OpenCode (repo `anomalyco/opencode`, antes `sst/opencode`) en sus dos líneas incompatibles, V1 (`1.19.x`) y V2 (`2.3.x`): plugins en proceso (server, TUI, GUI, RPC), hooks y eventos, skills, comandos, agentes, tools propios, MCP, instrucciones, permisos, temas, keybinds, LSP, formatters y distribución. Verificado sobre el código fuente (`dev`, commit `c98608c`, 2026-11-20) y los docs. Corte al 2026-11-19.

---

## 1. Visión General y Versiones

| | V1 | V2 |
| :--- | :--- | :--- |
| Versión | `v1.19.6` (2026-11-17, "Latest" en GitHub) | `v2.3.2` (2026-11-18) |
| Instalación | binario `opencode` | `curl -fsSL https://opencode.ai/v2/install`, `npm i -g @opencode/cli`, brew `anomalyco/tap/opencode-v2` |
| Superficies | TUI, `opencode serve` + SDK `@opencode-ai/sdk`, `opencode web`, ACP, extensión de IDE, GitHub/GitLab | CLI/TUI, **Desktop (Electron)**, Web (`opencode pair`), Docker `ghcr.io/anomalyco/opencode`, ACP, `@opencode/client`, Console |
| SDK de plugins | `@opencode-ai/plugin` | `@opencode/plugin` (exports `.`, `./effect`, `./tui`, `./gui`…) |

- V1 y V2 no se instalan lado a lado; comparten ubicaciones de config.
- V2 rompe intencionalmente tres cosas: API de plugins, API de servidor y config de terminal (`tui.json` → `cli.json`).
- Arquitectura: un servidor (core) con clientes (TUI, desktop, web). Los plugins pueden tener partes server, TUI, GUI y RPC.

Fuentes: [releases](https://github.com/anomalyco/opencode/releases), [docs](https://opencode.ai/docs/), [migrate-v1](https://opencode.ai/docs/migrate-v1/), [v1/ide](https://opencode.ai/v1/docs/ide/).

---

## 2. Modelo de Extensión

**Un plugin es un módulo JS/TS que corre en proceso.** No existen hooks por subprocess con JSON: para reutilizar un script externo (Python, shell), el plugin lo invoca con `child_process` / `Bun.$`.

### 2.1 Partes de un Plugin V2

| Parte | Export | Qué hace |
| :--- | :--- | :--- |
| Server | `.` o `./server` | Tools, hooks, integraciones, comandos, skills, MCP |
| TUI | `./tui` | Comandos, rutas, slots, renderers de Markdown, notificaciones |
| GUI | `./gui-experimental` | Desktop: `setup` por ventana y `main` en el proceso principal de Electron |
| RPC | `./rpc` | Contrato tipado entre partes |

En V1 un plugin es una función `Plugin = (input, options?) => Promise<Hooks>` o un objeto `{ id?, server, tui? }`; desde 1.19 existe un plugin TUI aparte (`@opencode-ai/plugin/tui`, configurado en `tui.json`, no descrito en la doc oficial V1).

Un mismo paquete puede servir a ambas líneas exportando `server()` (V1) y `setup()` (V2); requiere V1 ≥ **1.18.29** ([migrate-v1](https://opencode.ai/docs/build/plugins/migrate-v1/)).

### 2.2 Scopes y Precedencia

| Scope | Ubicación |
| :--- | :--- |
| Sistema | `/etc/opencode`, `/Library/Application Support/opencode`, `%ProgramData%\opencode` |
| Global | `~/.config/opencode/` |
| Proyecto | `opencode.json(c)`, `.opencode/opencode.json(c)` |
| Carpetas | `.opencode/{agents,commands,modes,plugins,skills,tools,themes}/` (también en singular) |

**V1** (menor → mayor): `.well-known/opencode` remota → global → `OPENCODE_CONFIG` → proyecto → `.opencode` → `OPENCODE_CONFIG_CONTENT` → archivos gestionados → MDM plist (macOS). [Fuente](https://opencode.ai/v1/docs/config/)

**V2**: global → `opencode.json` desde el ancestro más lejano al más cercano → todos los `.opencode/opencode.json` en el mismo orden → sistema → MDM (gana). Las configuraciones **se fusionan**; los arrays `plugins`, `skills` e `instructions` se combinan. [Fuente](https://opencode.ai/docs/config/)

---

## 3. Identidad del Plugin ("Manifiesto")

No hay `plugin.json` declarativo. La identidad sale de `package.json` y del código.

### 3.1 V1

```ts
import type { Plugin } from "@opencode-ai/plugin"

export const MyPlugin: Plugin = async (
  { project, client, $, directory, worktree, serverUrl, experimental_workspace },
  options,
) => ({ /* hooks */ })
```

- Config: `"plugin": ["pkg", "pkg@1.2.0", ["./plugin/local.ts", { opts }]]`.
- Dependencias de plugins locales: `.opencode/package.json` recibe `bun install` al arrancar. Los plugins npm se instalan con Bun en `~/.cache/opencode/node_modules/`.
- **`engines.opencode`** en `package.json`: el loader valida el rango semver y rechaza el plugin si no se cumple (`packages/opencode/src/plugin/shared.ts`, `checkPluginCompatibility`).
- **`oc-themes`** en `package.json`: un plugin TUI incluye archivos de tema.

### 3.2 V2

```ts
import { Plugin } from "@opencode/plugin"

export default Plugin.define({
  id: "acme.mi-plugin",
  async setup(ctx) {
    // registrar hooks, transforms, tools...
    return () => { /* cleanup */ }
  },
})
```

| Aspecto | Detalle |
| :--- | :--- |
| `id` | **Obligatorio.** Delimita `ctx.storage`, estado y diagnósticos |
| `package.json` | `type: "module"`, `exports` (`.`, `./tui`, `./gui-experimental`, `./rpc`), `files`, dependencia `@opencode/plugin` |
| Directorio local sin `exports` | Se resuelve por nombre de archivo: `server`/`index`, `tui`, `rpc`, `gui-experimental.cjs` |
| Config | `"plugins": ["pkg", "pkg@1.2.0", "./dir", "file://…", { "package": "…", "options": {…} }]` |
| Opciones | `ctx.options` |
| Habilitar / deshabilitar | `"-id"`, `"*"`, `"prefijo.*"`; un id posterior vuelve a habilitar. `opencode.config.policy` y `opencode.provider.opencode` ignoran las remociones |
| Ejemplo oficial | [`@opencode/plugin-figma`](https://github.com/anomalyco/opencode/tree/dev/packages/plugins/figma): `peerDependencies: {"@opencode/plugin": "^2.0.27"}`, dependencias npm propias, aporta skills y MCP |

> [!NOTE]
> Las carpetas `.opencode/plugins/` y `~/.config/opencode/plugins/` **autodescubren** archivos `.ts`/`.js` directos y directorios de paquete. La regla "solo directorios" aplica únicamente a rutas declaradas en `plugins` ([plugins](https://opencode.ai/docs/plugins/)).

---

## 4. Hooks y Eventos

### 4.1 V1

Tipos en [`v1.19.6/packages/plugin/src/index.ts`](https://github.com/anomalyco/opencode/blob/v1.19.6/packages/plugin/src/index.ts).

| Hook | Firma `(input, output)` | Qué se puede mutar |
| :--- | :--- | :--- |
| `event` | `{event}` | Solo lectura; todos los eventos del bus |
| `config` | `Config` | Config completa (inyectar agentes, comandos, MCP…) |
| `tool` | mapa `{nombre: tool()}` | Registra tools |
| `auth` / `provider` | métodos OAuth/API, modelos | Proveedores |
| `chat.message` | `{sessionID, agent, model, messageID}` / `{message, parts}` | Mensaje de usuario entrante |
| `chat.params` | / `{temperature, topP, topK, maxOutputTokens, options}` | Parámetros del LLM |
| `chat.headers` | / `{headers}` | Headers |
| `permission.ask` | `Permission` / `{status}` | Decisión. **No se dispara** ([#7006](https://github.com/anomalyco/opencode/issues/7006)) |
| `command.execute.before` | `{command, sessionID, arguments}` / `{parts}` | Prompt del comando |
| `tool.execute.before` | `{tool, sessionID, callID}` / `{args}` | `args`; `throw` bloquea |
| `tool.execute.after` | `{tool, sessionID, callID, args}` / `{title, output, metadata}` | Resultado |
| `shell.env` | `{cwd, sessionID?, callID?}` / `{env}` | Entorno del shell |
| `tool.definition` | `{toolID}` / `{description, parameters}` | Lo que el LLM ve de un tool |
| `experimental.chat.system.transform` | / `{system: string[]}` | System prompt |
| `experimental.chat.messages.transform` | / `{messages}` | Historial |
| `experimental.session.compacting` | `{sessionID}` / `{context[], prompt?}` | Prompt de compactación |
| `experimental.compaction.autocontinue` | / `{enabled}` | Turno "continue" sintético |
| `experimental.provider.small_model`, `experimental.text.complete` | | |
| `dispose` | | Limpieza |

**Eventos del bus V1:** `command.executed`, `file.edited`, `file.watcher.updated`, `installation.updated`, `lsp.client.diagnostics`, `lsp.updated`, `message.part.removed/updated`, `message.removed/updated`, `permission.asked/replied`, `server.connected`, `session.created/compacted/deleted/diff/error/idle/status/updated`, `todo.updated`, `shell.env`, `tool.execute.before/after`, `tui.prompt.append`, `tui.command.execute`, `tui.toast.show`. Logging con `client.app.log()`. [Fuente](https://opencode.ai/v1/docs/plugins/)

### 4.2 V2

Un objeto evento mutable por callback. Fuentes: [server](https://opencode.ai/docs/build/plugins/server/), [migrate-v1](https://opencode.ai/docs/build/plugins/migrate-v1/).

| Registro | Momento | Qué se puede mutar |
| :--- | :--- | :--- |
| `session.hook("prompt")` | Admisión del prompt | `prompt.text/files/skills/agents`, `metadata`, `delivery` (`steer`/`queue`) |
| `session.hook("context")` | Antes de cada llamada al modelo | `system`, `messages`, `tools` (solo borrar), `options` |
| `session.hook("compaction")` | Antes del resumen | Igual que `context` + `result` (permite saltarse el modelo) |
| `session.hook("generate" \| "title")` | Generación auxiliar o de título | `system`, `messages`, `options`, `result` |
| `session.hook("experimental.model.resolve")` | Resolución del modelo | `model` (mismo proveedor) |
| `session.hook("model.request" \| "http.request" \| "http.response")` | Despacho al modelo y HTTP | headers, request, response |
| `session.hook("experimental.ws.handshake/send/receive")` | WebSocket | url, headers, frame |
| `session.hook("retry")` | Tras clasificar un fallo | `decision` |
| `session.hook("fork")` | Tras copiar historial | — |
| `permission.hook("evaluate")` | Decisiones allow/ask (no un `deny` configurado) | `effect`, `message` |
| `shell.hook("create.before")` | Antes de crear un shell | `command`, `cwd`, `timeout`, `shell`, `env` |
| `tool.hook("execute.before")` | Antes de un tool | `input` reemplazable; `throw` rechaza la llamada |
| `tool.hook("execute.after")` | Después de un tool | `result` o `error` |
| `event.subscribe({types?, signal?})` | Stream del bus | Solo lectura |

**Transforms** (sincrónicos, reproducibles) sobre `agent`, `provider`, `model`, `command`, `integration`, `mcp`, `reference`, `skill`, `tool`, `vcs`, `websearch`, `worktree`. Reemplazan los hooks V1 `config`, `tool`, `auth`, `provider`. Sin equivalente V2: `experimental.compaction.autocontinue`, `experimental.provider.small_model`, `experimental.text.complete`.

**Eventos del bus V2** (derivados del código, no de la referencia `/api`): `session.created/deleted/idle/forked`, `session.execution.started/succeeded/failed/interrupted`, `session.step.started/ended/failed`, `session.tool.called/success/failed/progress`, `session.compaction.started/ended/failed`, `session.agent.selected`, `session.model.selected`, `session.instructions.updated`, `session.skill.activated`, `session.shell.started/ended`, `permission.asked/replied`, `form.created/replied/cancelled`, `mcp.status.changed`, `mcp.resources.changed`, `shell.created/exited/deleted`, `installation.updated`, `plugin.updated`, `skill.updated`, `command.updated`, `agent.updated`, `vcs.branch.updated`, `worktree.updated`, `project.updated`. No hay `file.edited` en V2.

### 4.3 Interceptar y Decidir sobre un Tool

```ts
// V2
setup(ctx) {
  ctx.tool.hook("execute.before", async (ev) => {
    // ev: { tool, input (mutable), sessionID, agent, messageID, id, codemode }
    if (peligroso(ev)) throw new Error("Bloqueado: motivo")      // bloquear
    await ctx.permission.assert({                                  // preguntar
      sessionID: ev.sessionID, agent: ev.agent,
      source: { type: "tool", messageID: ev.messageID, id: ev.id },
      action: "mi-plugin.shell", resources: [ev.input.command], metadata: { reason: "..." },
    })
  })
  ctx.permission.hook("evaluate", (ev) => { /* ev.effect = "allow" | "ask"; ev.message */ })
}
```

| Capacidad | V2 | V1 |
| :--- | :--- | :--- |
| Bloquear | `throw` (el modelo ve el mensaje y sigue) | `throw` |
| Preguntar | `permission.assert(...)` o `evaluate` → `ask` | **No confiable** (`permission.ask` no se dispara) |
| Auto-aprobar | `evaluate` → `effect = "allow"` (requiere correlación con la llamada) | — |
| Reescribir input | Mutar `ev.input` / `ev.tool` | Mutar `output.args` |
| Contexto al modelo antes del tool | No (solo `execute.after` o `session.hook("context")`) | No |
| Code mode | `execute.before` también se dispara para llamadas internas del tool `execute` (`codemode: true`) | — |

### 4.4 Tools Nativos y Argumentos

| Propósito | V2 | V1 |
| :--- | :--- | :--- |
| Shell | `shell` {`command`, `workdir`, `timeout`, `background`} (desde v2.2.5 `bash` se ejecuta como `shell`) | `bash` {`command`, …} |
| Lectura | `read` {`path`, `offset`, `limit`} | `read` {`filePath`, …} |
| Escritura | `write` {`path`, `content`} | `write` {`filePath`, `content`} |
| Edición | `edit` {`path`, `oldString`, `newString`, `replaceAll`} | `edit` {`filePath`, `oldString`, `newString`} |
| Patch | `patch` {`patchText`} (solo modelos GPT) | `apply_patch` {`patchText`} |
| Búsqueda | `grep`, `glob` | `grep`, `glob` |

`patchText` usa el sobre `*** Begin Patch` / `*** Add File:` / `*** Update File:` / `*** Move to:` / `*** Delete File:` con líneas `+`/`-` (mismo formato que Codex), rutas relativas al proyecto.

### 4.5 Mapeo con Claude Code

| Claude Code | V1 | V2 | ¿Bloquea o modifica? |
| :--- | :--- | :--- | :--- |
| `PreToolUse` | `tool.execute.before` | `tool.hook("execute.before")` | Bloquea con `throw`; muta args |
| `PostToolUse` | `tool.execute.after` | `tool.hook("execute.after")` | Muta resultado |
| `PermissionRequest` | `permission.ask` (roto) | `permission.hook("evaluate")` | V2 sí |
| `UserPromptSubmit` | `chat.message` | `session.hook("prompt")` | Muta el prompt; bloquear no verificado |
| `SessionStart` | evento `session.created` | evento `session.created` | Observa; contexto vía `system.transform` / `session.hook("context")` |
| `Stop` | evento `session.idle` | `session.idle` / `session.execution.succeeded` | No bloquea |
| `SubagentStop` | `session.idle` de la sesión hija | `session.execution.*` de la hija | No bloquea |
| `PreCompact` | `experimental.session.compacting` | `session.hook("compaction")` | Sí (V2 incluso el resultado) |
| `Notification` | `permission.asked` / `tui.toast.show` | `permission.asked`, `form.created`; TUI `attention.notify` | Observa |
| `SessionEnd` | `session.deleted` / `dispose` | `session.deleted` / cleanup de `setup` | Observa |

---

## 5. Skills

| Aspecto | V1 | V2 |
| :--- | :--- | :--- |
| Ubicaciones | `.opencode/skills/<n>/SKILL.md`, `~/.config/opencode/skills/`, `.claude/skills/`, `~/.claude/skills/`, `.agents/skills/`, `~/.agents/skills/` (proyecto: sube hasta el worktree) | Las mismas + `skills: [rutas \| URLs]` en config; catálogos HTTP con `index.json` y `version`; formato plano `skills/x.md` o `x/SKILL.md`; búsqueda hasta 6 niveles |
| Precedencia (menor → mayor) | — | built-in → `.claude/skills` → `.agents/skills` → `~/.config/opencode/skills` → `.opencode/skills` → `skills` de config |
| Identidad | `name` (`^[a-z0-9]+(-[a-z0-9]+)*$`, = directorio) | **ID por ruta**; `name` es solo etiqueta |
| Frontmatter | `name`, `description` (1–1024), `license`, `compatibility`, `metadata`. **`allowed-tools`, `model` y `disable-model-invocation` no tienen efecto** | `name`, `description`, `metadata.opencode/autoinvoke: false`, **`disable-model-invocation: true`**; `license`/`compatibility` sin efecto; `allowed-tools` y `model` no documentados |
| Invocación | Tool `skill({name})`, lista `<available_skills>` | Tool `skill({id})` o `@skill-id` |
| Permisos | `permission.skill` con patrones, por agente; `tools: { skill: false }` | `permissions: [{action:"skill", resource, effect}]`; política `skill.use` |
| Desde plugin | Hook `config` | `ctx.skill.transform(e => e.add(Skill.Info))` |
| Desactivar Claude | `OPENCODE_DISABLE_CLAUDE_CODE(_SKILLS)`, `OPENCODE_DISABLE_EXTERNAL_SKILLS` | Igual |

Fuentes: [v1/skills](https://opencode.ai/v1/docs/skills/), [skills](https://opencode.ai/docs/skills/).

---

## 6. Comandos Slash

| Aspecto | V1 | V2 |
| :--- | :--- | :--- |
| Archivos | `.opencode/commands/*.md`, `~/.config/opencode/commands/` | Igual + subcarpetas (`team/review.md` → `/team/review`) |
| Config | `command: {nombre: {template, description, agent, model, subtask}}` | `commands` (plural); `subagent` reemplaza a `subtask`; `model` acepta `provider/model#variant` |
| Placeholders | `$ARGUMENTS`, `$1`…`$n` | Igual; el último placeholder consume el resto; sin placeholder, se agregan al final |
| Inyecciones | `` !`cmd` `` (salida de shell), `@archivo` | `` !`cmd` ``; **`@path` ya no se expande** |
| Otros | Puede sobrescribir built-ins | Recarga en caliente |
| Desde plugin server | Solo hook `config` o `command.execute.before` | `ctx.command.transform(e => e.add({name, description, execute({sessionID, prompt, delivery})}))` |
| Desde plugin TUI | `TuiCommand.slash` | `keymap.layer({commands: [{slash: {name, aliases, arguments}, run}]})` |
| Lee `.claude/commands` | No | No |

Fuentes: [v1/commands](https://opencode.ai/v1/docs/commands/), [commands](https://opencode.ai/docs/commands/), [plugins/cli](https://opencode.ai/docs/build/plugins/cli/).

---

## 7. Agentes, Subagentes y Modos

| Aspecto | V1 | V2 |
| :--- | :--- | :--- |
| Ubicaciones | `.opencode/agents/*.md` (también `agent/`, `mode/`, `modes/`), `agent: {}` en config | `agents: {}` o Markdown; ID anidado `team/reviewer` |
| Frontmatter | `description`, `mode` (primary/subagent/all), `model`, `variant`, `temperature`, `top_p`, `prompt` (`{file:}`), `permission`, `tools` (obsoleto), `maxSteps`, `hidden`, `color`, `disable`, permisos de `task` | `description`, `mode`, `model#variant`, `system` (= cuerpo), `permissions[]`, `steps`, `hidden`, `color`, `disabled`, `request` (preservado, aún no se envía) |
| Invocación | Tab (primarios); subagentes automáticos por `description` o `@nombre` | Tool `subagent` (también en background); acción de permiso `subagent` reemplaza a `task`; `experimental.subagent_depth` (1) |
| Built-ins | — | `build`, `plan`, `general`, `explore` |
| Desde plugin | Hook `config` | `ctx.agent.transform` (expone `update`, `remove`, `default`; **no hay `add`** documentado) |
| Lee `.claude/agents` | No | No |

El frontmatter V1 se traduce en V2 si el archivo no mezcla formatos. [Fuente](https://opencode.ai/docs/agents/)

---

## 8. Tools Propios y MCP

### 8.1 Tools Propios

- **V1:** archivos `.opencode/tools/*.ts` y `~/.config/opencode/tools/`. El nombre de archivo es el nombre del tool (varios exports → `archivo_export`). Helper `tool({description, args: tool.schema.* (Zod), execute(args, {agent, sessionID, messageID, directory, worktree})})`. **Mismo nombre que un built-in lo reemplaza.** También vía hook `tool` de un plugin. Pueden envolver Python con `Bun.$`. [Fuente](https://github.com/anomalyco/opencode/blob/dev/services/site/src/docs/v1/content/custom-tools.mdx)
- **V2:** `ctx.tool.transform(e => { e.namespace({name, description}); e.add({name, description, input: JSONSchema | Schema.Struct, options: {namespace, codemode}, freeform?, execute(input, {progress, signal}) => ({content})}) })`. Nombre efectivo `namespace_name`; `freeform` acepta text, regex o lark. No hay descubrimiento de `.opencode/tools/` en V2 (no verificado en ejecución).

### 8.2 MCP

| Aspecto | V1 | V2 |
| :--- | :--- | :--- |
| Config | `mcp: {nombre: {type: local\|remote, command, environment, enabled, url, headers, oauth: {clientId,…}, timeout}}` | `mcp.servers.{nombre}`: `disabled` (reemplaza `enabled`), `cwd`, `environment`, `codemode` (por defecto expone vía Code Mode), `timeout: {catalog, execution}`, `protocol`, `pool` |
| OAuth | Sí | PKCE, CIMD/DCR/cliente pre-registrado (`client_id`, `client_secret`, `scope`, `callback_port`, `redirect_uri`, `auth_server_metadata_url`) |
| CLI | — | `opencode mcp add/list/auth/logout` |
| Variables | `{env:VAR}` | `{env:VAR}` |
| Permisos | `<server>_<tool>` | `<server>_<tool>`; política `mcp.use` |
| Desde plugin | Hook `config` | `ctx.mcp.transform(e => e.set(...))` |

Fuentes: [mcp-servers](https://opencode.ai/docs/mcp-servers/), [v1/mcp-servers](https://opencode.ai/v1/docs/mcp-servers/).

---

## 9. Instrucciones y Reglas

- `AGENTS.md`: el global `~/.config/opencode/AGENTS.md` más los de cada directorio desde el actual hasta home o la raíz del proyecto.
- **Fallback a `CLAUDE.md`** por directorio; `~/.claude/CLAUDE.md` si falta el `AGENTS.md` global.
- `instructions: [archivos, globs, ~/…, URLs]` (URLs con timeout de 5 s).
- **V1:** gana el primer archivo encontrado al subir el árbol. **V2:** elige por directorio (un `AGENTS.md` raíz ya no oculta un `CLAUDE.md` anidado), descubre `AGENTS.md` anidados de forma perezosa al leer, recarga en caliente. Orden documentado: agent → codemode → MCP/refs/skills → AGENTS.md → instructions → entorno → entradas por API.
- `OPENCODE_DISABLE_CLAUDE_CODE_PROMPT`: V1 omite solo `~/.claude/CLAUDE.md`; V2 omite **todos** los `CLAUDE.md`.
- **Desde plugin:** V1 `experimental.chat.system.transform`; V2 `session.hook("context")` (`event.system.push`), `ctx.session.instructions.entry.put` (bloques `<context key>`), `ctx.reference.add`. **Un `.md` de reglas dentro del paquete no se carga solo.**

Fuentes: [instructions](https://opencode.ai/docs/instructions/), [v1/rules](https://github.com/anomalyco/opencode/blob/dev/services/site/src/docs/v1/content/rules.mdx).

---

## 10. Permisos Nativos (V2)

Fuente: [permissions](https://opencode.ai/docs/permissions/).

- Decisiones `allow` / `ask` / `deny` con globs `*` y `?` sobre el valor completo. **Gana la última regla que coincide**; con varios recursos, cualquier `deny` gana.
- Acciones: `read`, `edit` (edit, write y patch), `shell` (antes `bash`), `glob`, `grep`, `webfetch`, `websearch`, `external_directory`, `subagent`, `skill`, `execute`, MCP `<server>_<tool>`. Por agente en `agents.<id>.permissions`.
- Defaults: `*` allow; `*.env` y `*.env.*` en **ask**; `*.env.example` allow; `external_directory` **allow** (V1: ask).
- Diálogo: `once`, `always` (guarda allow por proyecto), `reject`.
- **Políticas** (`plugin.use`, `skill.use`, `mcp.use`, `tool.use`, `provider.use`, `integration.*`, `websearch.use`): solo niegan ([policies](https://opencode.ai/docs/policies/)).

---

## 11. Configuración del Plugin y Datos

| Claude Code | OpenCode |
| :--- | :--- |
| `userConfig` | **Sin equivalente declarativo** ni prompts de instalación. Opciones: V1 segundo argumento (`["pkg", {opts}]`), V2 `ctx.options` (`{package, options}`) |
| Secretos | `{env:VAR}` y `{file:ruta}` en config. V2: `ctx.integration` (`connect.key`, oauth), `ctx.credential`. V1: hook `auth` (proveedores) |
| `${CLAUDE_PLUGIN_DATA}` | V2 server: `ctx.storage.get/set/remove/scan` (JSON en el KV del servidor con namespace por `id`; **no es un directorio**, `packages/core/src/plugin/host.ts`). V2 TUI: `context.storage.store(key, {initial})`. V1 TUI: `api.kv`. V1 server: nada |
| `${CLAUDE_PLUGIN_ROOT}` | `import.meta.url` |
| Ubicación del proyecto | V2 `ctx.location.{directory, project}`; V1 `directory`, `worktree` |
| `bin/` en PATH | Inyectar `PATH` con `shell.env` (V1) o `shell.hook("create.before").env` (V2) |
| Settings por defecto | V1 hook `config`; V2 transforms por dominio |

---

## 12. Mods de Interfaz

### 12.1 Temas

- **V1:** JSON en `~/.config/opencode/themes/` o `.opencode/themes/`, formato `{defs, theme: {token: {dark, light}}}`, colores ANSI 0–255 y `none`; selección en `tui.json`. Un plugin TUI los aporta con `oc-themes` o `theme.install(jsonPath)`. [Fuente](https://opencode.ai/v1/docs/themes/)
- **V2:** formato `{base, light, dark}` con `hue` y referencias `$hue.cyan`; selección en `cli.json` (`theme: {name, mode}`). **La API TUI V2 no registra temas** (solo archivos). [Fuentes](https://opencode.ai/docs/themes/) · [cli/theme](https://opencode.ai/docs/cli/theme/)

### 12.2 Keybinds

V1 `tui.json` → `keybinds`; V2 `cli.json` → `keybinds` (IDs tipo `command.palette.show`) + `leader`. [Fuente](https://opencode.ai/docs/cli/keybinds/)

### 12.3 Plugin TUI

**V2** (`@opencode/plugin/tui`, Solid + OpenTUI; [plugins/cli](https://opencode.ai/docs/build/plugins/cli/)):

| API | Uso |
| :--- | :--- |
| `ui.toast`, `ui.dialog.{alert, confirm, prompt, select}` | Notificaciones y diálogos |
| `ui.router.register`, `ui.tabs`, `ui.panel.open` | Rutas, páginas, pestañas, paneles de sesión |
| **Slots** `app`, `home.footer`, `home.footer.status`, `prompt.footer(.status\|.file)`, `session.composer.top`, `sidebar.content`, `sidebar.footer`, `session.panel` | **Equivalente a la statusline** |
| `keymap.layer` | Comandos, slash, binds |
| `markdown.registerCodeBlockRenderer` | Renderers de bloques de código |
| `attention.notify` | Notificación del sistema y sonido |
| `data.on/listen`, `ui.model.variant` | Eventos y estado |

**V1** (`@opencode-ai/plugin/tui`): `command.register`, `route`, `ui` (dialogs, toast), slots (`home_footer`, `sidebar_content`, `session_prompt_right`…), `theme.install`, `attention`, `kv`, `event.on` ([tui.ts](https://github.com/anomalyco/opencode/blob/v1.19.6/packages/plugin/src/tui.ts)).

### 12.4 GUI (Desktop, experimental)

Paneles, comandos, páginas de settings, slots, IPC, menús nativos. Solo se configura en la config **global**. [Fuente](https://opencode.ai/docs/build/plugins/gui/)

### 12.5 Output Styles

Sin componente propio: `system` del agente o `session.hook("context")`.

---

## 13. LSP, Formatters y Segundo Plano

| Componente | V1 | V2 |
| :--- | :--- | :--- |
| LSP | `lsp: true \| {id: {command, extensions, env, initialization, disabled}}`, deshabilitado por defecto; 34 servidores incluidos; `OPENCODE_DISABLE_LSP_DOWNLOAD`; tool `lsp` experimental (`OPENCODE_EXPERIMENTAL_LSP_TOOL`) ([v1/lsp](https://opencode.ai/v1/docs/lsp/)) | **No se ejecuta**: la clave `lsp` se acepta y preserva, sin servidores ni diagnósticos ([migrate-v1](https://opencode.ai/docs/migrate-v1/)) |
| Formatters | `formatter: true \| {id: {command: [..., "$FILE"], extensions, environment, disabled}}`, corren tras `write`/`edit`/`patch` | Igual, deshabilitados por defecto ([formatters](https://opencode.ai/docs/formatters/)) |
| Segundo plano | `$` de Bun / child_process + hook `event` | Procesos en `setup` (limpiados en el cleanup), `event.subscribe`, tool `shell` con `background`, comandos con `subagent: true` |

No hay "monitores" declarativos.

---

## 14. Distribución

| Aspecto | V1 | V2 |
| :--- | :--- | :--- |
| Fuentes | npm (Bun instala al arrancar en `~/.cache/opencode/node_modules/`) | npm (versión, tag o rango) y **Git** (atajos de GitHub, HTTPS, SSH, repos privados) |
| CLI | `opencode plugin <módulo> [-g] [-f]` | `opencode plugin add\|list [--builtin]\|check\|update\|remove` |
| Versionado | Sin versión → `@latest`; con versión → fijado | Versiones exactas y commits completos fijados; `name@latest` se autoactualiza al arrancar y cada 24 h con rollback si falla |
| Carga | — | Paquetes en caché cargan de inmediato; faltantes se instalan en background |
| Oficiales | — | `packages/plugins/*`, instalados desde URL de Git |
| Catálogo | [Ecosystem](https://opencode.ai/v1/docs/ecosystem/) (43 plugins), [awesome-opencode](https://github.com/awesome-opencode/awesome-opencode), [opencode.cafe](https://opencode.cafe) | Igual. **Sin marketplace oficial con manifiesto** |
| Enterprise | `/etc/opencode`, MDM `ai.opencode.managed`, `.well-known/opencode` remota ([v1/enterprise](https://opencode.ai/v1/docs/enterprise/)) | `/etc/opencode/opencode.json(c)` con `agents/ commands/ skills/ plugins/`; MDM; well-known vía `/connect`; políticas que solo niegan |

---

## 15. Compatibilidad Directa con Plugins de Claude Code

| Lee | No lee |
| :--- | :--- |
| Skills en `.claude/skills` y `~/.claude/skills` (V2 respeta `disable-model-invocation`) | `.claude/agents`, `.claude/commands` |
| `CLAUDE.md` y `~/.claude/CLAUDE.md` como fallback | Hooks, `settings.json`, `.mcp.json`, `.claude-plugin/plugin.json` |

Verificado en V2: `config/plugin/compatibility.ts` solo escanea `<.claude|.agents>/skills`; `instruction-discovery.ts` solo trata `CLAUDE.md`. El proveedor "Claude Agent" (que ejecuta Claude Code) tampoco carga hooks de Claude.

Portar un plugin de Claude a OpenCode significa: skills y `CLAUDE.md` funcionan; comandos y agentes se copian a `.opencode/` adaptando el frontmatter; MCP se traduce a `mcp`/`mcp.servers`; hooks se reescriben como plugin TS (o un shim TS que invoque los scripts originales con el payload traducido).

---

## 16. Tabla de Compatibilidad por Componente

| Claude Code | OpenCode | ¿Soportado? | Notas |
| :--- | :--- | :---: | :--- |
| `.claude-plugin/plugin.json` | `package.json` + `Plugin.define({id})` / función V1 | Parcial | Sin manifiesto declarativo de componentes |
| `userConfig` | `options` → `ctx.options` | Parcial | Sin prompts ni schema |
| Secretos | `{env:}`, `{file:}`, `ctx.integration`/`credential`, hook `auth` | Parcial | |
| Skills | Nativas + `.claude/skills` | Sí | V2: ID por ruta; `allowed-tools`/`model` no aplican |
| Comandos | `.opencode/commands/*.md`, `commands` | Sí | No lee `.claude/commands`; `$ARGUMENTS`, `$1`, `` !`cmd` `` |
| Subagentes | `.opencode/agents/*.md`, `agents` | Sí | Frontmatter distinto; plugin no puede añadir (no verificado) |
| `PreToolUse` | `tool.execute.before` / `execute.before` | Sí | Solo en proceso |
| `PostToolUse` | `tool.execute.after` / `execute.after` | Sí | |
| `PermissionRequest` | `permission.hook("evaluate")` | V2 sí, V1 no | |
| `UserPromptSubmit` | `chat.message` / `session.hook("prompt")` | Sí | |
| `SessionStart`, `Stop`, `SubagentStop`, `SessionEnd`, `Notification` | Eventos del bus | Parcial | Solo observan |
| `PreCompact` | `experimental.session.compacting` / `session.hook("compaction")` | Sí | |
| MCP | `mcp` / `mcp.servers` / `ctx.mcp.transform` | Sí | OAuth incluido |
| LSP | `lsp` | V1 sí, V2 no | |
| Monitores | Procesos en `setup`, `event.subscribe` | Parcial | Imperativo |
| `bin/` en PATH | `shell.env` / `shell.hook("create.before")` | Parcial | Manual |
| Settings por defecto | Hook `config` / transforms | Parcial | |
| Output styles | `system` del agente / `session.hook("context")` | Parcial | |
| Temas | `themes/*.json`; V1 `oc-themes`/`theme.install` | Sí | V2 solo archivos |
| Statusline | Slots TUI | Sí (plugin TUI) | |
| `${CLAUDE_PLUGIN_ROOT}` / `_DATA` | `import.meta.url` / `ctx.storage` | Parcial | Sin variables de entorno |
| Marketplaces | npm, Git, ecosystem | Parcial | Sin marketplace oficial |
| Instalación / actualización | `opencode plugin add/update`, `@latest` | Sí | |
| Scopes | Global, proyecto, sistema, MDM, políticas | Sí | GUI V2 solo global |
| `CLAUDE.md` | Fallback de `AGENTS.md` | Sí | |

---

## 17. No Verificado

- Si `session.hook("prompt")` puede rechazar un prompt con `throw`.
- Si `ctx.agent.transform` puede crear agentes nuevos.
- Ausencia de tools por archivo `.opencode/tools/` en V2 (no probado en ejecución).
- Lista estable de eventos V2 (derivada del código).
- Ruta en disco de la caché de plugins V2 y si los locales reciben `bun install`.
- Si V2 valida `engines.opencode`.
- Soporte de IDE en V2.
- Si V1 `permission.ask` se arregló ([#7006](https://github.com/anomalyco/opencode/issues/7006); PRs [#7077](https://github.com/anomalyco/opencode/pull/7077) y [#12742](https://github.com/anomalyco/opencode/pull/12742) cerrados sin merge confirmado).
- `.well-known/opencode` como capa de config en V2.
- Latencia real de invocar un proceso externo (Python) por llamada desde un plugin.
