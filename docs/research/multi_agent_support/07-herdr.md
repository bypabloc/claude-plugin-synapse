# Herdr

> Superficie completa de extensibilidad de Herdr, un **multiplexor de agentes de código** (no un agente): Herdr TUI (`herdr` 0.9.5), herdr-core (`hc` 0.1.21) y Herdr app (0.1.16). Plugins `herdr-plugin.toml`, 22 eventos, socket API, integraciones con cada agente, protocolo OSC 7501, mods JS de la app, configuración, distribución, y cómo afecta a los plugins de los agentes que hospeda. Verificado sobre el código de `herdrdev/*`. Corte al 2026-11-19.

---

## 1. Qué es Herdr y Versiones

Herdr no ejecuta tools: corre Claude Code, Codex, OpenCode, Cursor Agent, Copilot CLI, Antigravity CLI y otros dentro de terminales reales, muestra su estado y permite que un agente controle a otros. **Los plugins de cada agente siguen funcionando dentro de sus panes**; Herdr agrega una capa de orquestación encima.

| Producto | Repo / binario | Versión | Superficie de extensión |
| :--- | :--- | :--- | :--- |
| **Herdr TUI** (Rust) | `herdrdev/herdr`, `herdr` | 0.9.5 (2026-10-14) | Plugins `herdr-plugin.toml`, socket API NDJSON, CLI, integraciones por agente, manifiestos de detección, skill, `config.toml` |
| **herdr-core** (runtime de terminales sin UI) | `herdrdev/herdr-core`, `hc` | 0.1.21 (2026-11-12) | JSON-RPC 2.0 (OpenRPC), OSC 7501, templates de agente, documentos (`doc.get/set`), skill |
| **Herdr app** (escritorio, GPUI) | `herdrdev/herdr-app`, `herdr-app` | 0.1.16 (2026-11-14) | **Mods JS**, layouts JSON, `keys.json`, `settings.json`, `programs.json`, socket de control, skill |

- herdr-core y herdr-app se crearon el 2026-10-16 (Apache-2.0). La app es cliente de `hc` y **no lee el `config.toml` de la TUI**.
- La skill de herdr-core pide usar `hc`, nunca `herdr`, cuando `$HC_TERMINAL_ID` está definido.
- Fuentes: [herdr](https://github.com/herdrdev/herdr), [herdr-core](https://github.com/herdrdev/herdr-core), [herdr-app](https://github.com/herdrdev/herdr-app), [docs](https://herdr.dev/docs/), [Thoughtworks Radar](https://www.thoughtworks.com/radar/tools/herdr).

```text
+-------------------------------- Herdr TUI / App ---------------------------------+
|  sidebar: estado por agente (idle | working | blocked | done)                     |
|  +-------------------------+  +-------------------------+  +-------------------+ |
|  | pane 1: claude          |  | pane 2: codex           |  | pane 3: zsh       | |
|  |  hooks y plugins de     |  |  hooks y plugins de     |  |  sin guardrails   | |
|  |  Claude Code (intactos) |  |  Codex (intactos)       |  |                   | |
|  +-----------+-------------+  +-------------------------+  +---------^---------+ |
|              |  herdr pane run 3 "…" / agent send-keys / hc file write           |
|              +-------------------------------------------------------+           |
|               lo que se ejecuta en otro pane NO pasa por los hooks del agente     |
+----------------------------------------------------------------------------------+
   plugins: [[events]] [[actions]] [[panes]]      socket: ~/.config/herdr/herdr.sock
```

---

## 2. Modelo de Extensión y Directorios

### 2.1 TUI

| Mecanismo | Ubicación |
| :--- | :--- |
| Config | `~/.config/herdr/config.toml` (`%APPDATA%\herdr\config.toml`); `HERDR_CONFIG_PATH` la reemplaza ([configuration](https://herdr.dev/tui/docs/configuration/)) |
| Plugins | Globales al usuario. Código en `~/.config/herdr/plugins/` (`github/<id>`, `github-installations/<id>/<pid>-<gen>`, `.locks`) ([plugin_paths.rs](https://github.com/herdrdev/herdr/blob/master/src/plugin_paths.rs)) |
| Config / estado por plugin | `~/.config/herdr/plugins/config/<id>`; `<state_dir>/plugins/<id>` |
| Manifiestos de detección | Override local en `~/.config/herdr/agent-detection/<agent>.toml` |
| Integraciones | Scripts escritos en el directorio de cada agente (§4.4) |
| Socket | `~/.config/herdr/herdr.sock` o `~/.config/herdr/sessions/<name>/herdr.sock` |
| Skill | `skills/herdr/SKILL.md`; `herdr --skill` |
| Logs | `~/.config/herdr/herdr{,-client,-server}.log` |

### 2.2 herdr-core

Config `~/.config/herdr-core/config.toml` (`shell`, `restore_screen`, `notes_quiet`, `draft_wait`); templates `~/.config/herdr-core/templates/*.md`; estado `~/.local/state/herdr-core/<session>/`; socket `$XDG_RUNTIME_DIR/herdr-core/<session>.sock` (`HC_SOCKET`, `HC_SESSION`) ([security](https://github.com/herdrdev/herdr-core/blob/master/docs/security.md)).

### 2.3 App

Home `~/.config/herdr-app` (`HERDR_APP_HOME`): `layouts/`, `keys.json`, `settings.json`, `programs.json`, `mods/<name>/`, `shared.json`, `data/`, `AGENTS.md`, tipos `.d.ts` generados, socket de control local ([app security](https://github.com/herdrdev/herdr-app/blob/master/docs/security.md)).

---

## 3. Manifiesto `herdr-plugin.toml` (TUI)

Schema real ([manifest.rs](https://github.com/herdrdev/herdr/blob/master/src/app/api/plugins/manifest.rs)). Los metadatos van al **nivel superior** (no hay tabla `[plugin]`).

| Campo | Requerido | Notas |
| :--- | :--- | :--- |
| `id` | Sí | ≤ 120 caracteres, `[A-Za-z0-9.:_-]` |
| `name` | Sí | |
| `version` | Sí | |
| `min_herdr_version` | Según docs (opcional en código) | Semver; si es más nuevo que el binario: `plugin_requires_newer_herdr` |
| `description` | No | |
| `platforms` | No | `linux` / `macos` / `windows` |

| Sección | Campos | Semántica |
| :--- | :--- | :--- |
| `[[build]]` | `command` | Solo en `plugin install` desde GitHub, tras confirmar; sin contexto ni socket; si falla, aborta. `link` no la ejecuta |
| `[[startup]]` | `command` | Una vez por plugin habilitado, tras restaurar la sesión con el socket listo (también en *live handoff*); no en attach, reload, link ni enable. `HERDR_PLUGIN_EVENT=startup`. **No es un daemon** |
| `[[actions]]` | `id` (sin puntos), `title`, `description?`, `contexts?` (`global`, `workspace`, `tab`, `pane`, `selection`), `command` | Nombre calificado `plugin.id.action` |
| `[[events]]` | `on`, `command` | `on` debe ser uno de los 22 eventos (§4.1) |
| `[[panes]]` | `id`, `title`, `description?`, `placement` (`overlay` \| `popup` \| `split` \| `tab` \| `zoomed`), `width?`/`height?` (solo popup), `command` | Popup modal sin pane ID; `ui_busy` si hay otro modal |
| `[[link_handlers]]` | `id`, `title`, `pattern` (regex Rust), `action` | Ctrl+click con `ui.mouse_capture`; desde 0.9.0 también OSC 8 `file://` |

Todas las secciones aceptan `platforms`. `command` es siempre un **argv** (sin shell); los ejecutables relativos se resuelven desde la raíz del plugin (0.8.0+); en Windows se resuelven shims de `PATHEXT`.

```toml
id = "example.layout"
name = "Layout"
version = "0.1.0"
min_herdr_version = "0.7.0"
platforms = ["linux", "macos", "windows"]

[[build]]
command = ["npm", "ci"]

[[startup]]
command = ["node", "dist/restore.js"]

[[actions]]
id = "apply"
title = "Apply layout"
contexts = ["workspace"]
command = ["node", "dist/apply.js"]

[[events]]
on = "worktree.created"
command = ["herdr", "workspace", "list"]

[[panes]]
id = "board"
title = "Project board"
placement = "overlay"
command = ["herdr-board"]

[[link_handlers]]
id = "github-issue"
title = "Open GitHub issue"
pattern = "^https://github\\.com/[^/]+/[^/]+/(issues|pull)/[0-9]+$"
action = "apply"
```

Atajo a una acción del plugin (va en **`config.toml`**, no en el manifiesto):

```toml
[[keys.command]]
key = "prefix+l"
type = "plugin_action"
command = "example.layout.apply"
description = "apply layout"
```

**Validación:** ids únicos por tipo; si el manifiesto cambia tras el preview de instalación, se aborta; errores `plugin_manifest_parse_failed`, `invalid_plugin_id`, `duplicate_plugin_action_id`, `invalid_plugin_source`… En v1 no hay registro de acciones en runtime, UI nativa no terminal ni API de almacenamiento; **"The entire Herdr CLI is the plugin API"** ([plugins](https://herdr.dev/docs/plugins/)).

---

## 4. Eventos, Hooks e Integraciones

### 4.1 Eventos para `[[events]] on`

[events.rs](https://github.com/herdrdev/herdr/blob/master/src/api/schema/events.rs):

| Grupo | Eventos |
| :--- | :--- |
| Workspace | `workspace.created`, `.updated`, `.closed`, `.renamed`, `.moved`, `.reordered`, `.focused` |
| Worktree | `worktree.created`, `.opened`, `.removed` |
| Tab | `tab.created`, `.closed`, `.renamed`, `.moved`, `.focused` |
| Pane | `pane.created`, `.closed`, `.focused`, `.moved`, `.exited`, `.agent_detected`, `.agent_status_changed` |

Solo por socket (no hooks de plugin): `workspace.metadata_updated`, `pane.updated`, `pane.output_changed`, `pane.output_matched`, `pane.scroll_changed`, `layout.updated`.

**Los hooks de plugin no devuelven nada**: corren después del hecho y solo dejan log. No hay block, ask ni allow.

### 4.2 Payload y Variables de Entorno

- `HERDR_PLUGIN_EVENT_JSON` = `{"event": "<dot.name>", "data": {...}}`. Documentados: `worktree.created` `{workspace, worktree}`, `worktree.opened` `{workspace, worktree, already_open}`, `worktree.removed` `{workspace_id, worktree, forced}`, `workspace.moved` `{workspace_id, insert_index, workspaces}`, `tab.moved` `{tab_id, workspace_id, insert_index, tabs}`; el resto con `herdr api schema --json`.
- Comunes: `HERDR_SOCKET_PATH`, `HERDR_BIN_PATH`, `HERDR_ENV=1`, `HERDR_PLUGIN_ID`, `HERDR_PLUGIN_ROOT`, `HERDR_PLUGIN_CONFIG_DIR`, `HERDR_PLUGIN_STATE_DIR`, `HERDR_PLUGIN_CONTEXT_JSON`, `HERDR_{WORKSPACE,TAB,PANE}_ID`.
- Por tipo: `HERDR_PLUGIN_ACTION_ID`, `HERDR_PLUGIN_EVENT(_JSON)`, `HERDR_PLUGIN_ENTRYPOINT_ID`, `HERDR_PLUGIN_CLICKED_URL`, `HERDR_PLUGIN_LINK_HANDLER_ID`.
- `HERDR_PLUGIN_CONTEXT_JSON`: `workspace_id/label/cwd`, `worktree`, `tab_id/label`, `focused_pane_id/cwd/agent/status`, `selected_text`, `invocation_source`, `correlation_id`, `clicked_url`, `link_handler_id` ([context.rs](https://github.com/herdrdev/herdr/blob/master/src/app/api/plugins/context.rs)).
- `[[keys.command]]` recibe otro conjunto: `HERDR_ACTIVE_{WORKSPACE,TAB,PANE}_ID`, `HERDR_ACTIVE_PANE_CWD`.

### 4.3 Eventos de herdr-core

`created`, `lost`, `restarted`, `keep`, `finished`, `removed`, `status`, `turn`, `title`, `labels`, `size`, `foreground`, `doc`, más `heartbeat` y `missed` ([schema](https://github.com/herdrdev/herdr-core/blob/master/schema/README.md)).

### 4.4 Hooks que Herdr Instala en Cada Agente

`herdr integration install <agente>` ([integrations](https://herdr.dev/docs/integrations/)). Todos **solo reportan** al socket:

| Agente | Archivo / configuración | Evento | Reporta |
| :--- | :--- | :--- | :--- |
| Claude Code | `$CLAUDE_CONFIG_DIR/hooks/herdr-agent-state.sh` + `settings.json` (`HERDR_INTEGRATION_VERSION=11`; ignora eventos de Cursor y de subagentes) | `SessionStart` (startup/resume/clear/compact/fork) | Sesión; estado leído de pantalla |
| Codex | `$CODEX_HOME/herdr-agent-state.sh`, `hooks.json`, `[features] hooks = true` | Sesión (desde 0.9.5 sin turn hooks) | Sesión |
| Copilot | `$COPILOT_HOME/hooks/…sh` + `settings.json` | `SessionStart` | Sesión |
| Cursor | `$CURSOR_CONFIG_DIR/herdr-agent-state.sh` + `hooks.json` | `sessionStart` | Sesión |
| OpenCode | `~/.config/opencode/plugins/herdr-agent-state.js`, `herdr-tui-session.js`, `herdr-opencode/tui.js`; registro en `tui.jsonc`/`cli.json` | Plugin JS | Estado y sesión |
| Antigravity CLI | `$ANTIGRAVITY_CLI_CONFIG_DIR/hooks/…sh` + bloque `herdr` en `hooks.json` | `PreInvocation` | Conversación |

También existen integraciones para pi, omp, devin, droid, kimi, kilo, hermes, qodercli, qwen, letta, mastracode y grok. El script de Claude escribe la entrada del hook en `mktemp ${TMPDIR:-/tmp}` (contiene el `session_id`).

---

## 5. Acciones, Atajos y Skill

- **Acciones:** `herdr plugin action list/invoke`; se asocian a teclas con `[[keys.command]] type = "plugin_action"`.
- **`[[keys.command]]`**: `type` = `popup` | `pane` | `shell` | `plugin_action`, más `width`/`height`, `description` (aparece en `prefix+?`).
- **Paleta de comandos:** existe en la app; en la TUI no se verificó una que liste acciones de plugins.
- **Skill de Herdr** ([agent-skill](https://github.com/herdrdev/herdr/blob/master/docs/next/website/src/content/docs/agent-skill.mdx)): `npx skills add herdrdev/herdr --skill herdr -g` o `herdr --skill`. Si `HERDR_ENV ≠ 1`, el agente debe detenerse. Enseña `pane split/run/read/wait-output`, `agent start/prompt/wait/send-keys`, workspaces y worktrees. Pide "inspect the blocked UI and ask the user before answering it" (solo texto, no control).
- Otras guías: `https://herdr.dev/agent-guide.md`, `hc --skill`, `herdr-app --skill`.
- **Un plugin de Herdr no empaqueta skills** para los agentes.

---

## 6. Agentes: Detección, Estados y Autorreporte

- **`agent start`:** `herdr agent start <name> --kind KIND --pane ID [--timeout MS] [-- <agent-args...>]`. **Lo que va después de `--` son argumentos del ejecutable del agente** (por ejemplo `-- --dangerously-skip-permissions` llega a `claude`), no un comando libre. El pane debe existir y tener una shell libre. `<name>` cumple `[a-z][a-z0-9_-]{0,31}`; `agent_not_ready` si arranca bloqueado ([cli-reference](https://github.com/herdrdev/herdr/blob/master/docs/next/website/src/content/docs/cli-reference.mdx)).
- **Kinds:** pi, claude, codex, gemini, cursor, devin, agy, cline, omp, mastracode, opencode, copilot, kimi, kiro, droid, amp, grok, hermes, kilo, qodercli, qwen, letta, maki, muse.
- **Estados:** `idle`, `working`, `blocked`, `done`, `unknown` (agregación blocked > done > working > idle). *"Idle status is not authorization to approve an action"* ([agents](https://herdr.dev/docs/agents/)).
- **Detección (TUI):** proceso + reglas regex sobre la pantalla en manifiestos TOML (`distribution/agent-detection/<agent>.toml`, `[[rules]] id/state/priority/region/regex/any/not`), actualizados desde herdr.dev (`[update] manifest_check`) con override local. `HERDR_AGENT=<agent>` como pista para wrappers.
- **Autorreporte (TUI):** `herdr pane report-agent <pane> --source ID --agent L --state … [--seq N] [--message] [-- RESUME…]`, `report-agent-session`, `release-agent` (o por socket `pane.report_agent`…). `--source` no puede empezar con `herdr:`; el resume empieza con un comando del `PATH`, ≤ 64 argumentos y 8 KiB. Reportar solo si `HERDR_ENV=1` ([add-herdr-support](https://herdr.dev/tui/docs/add-herdr-support/)).
- **OSC 7501 (herdr-core y app)**, deducido del encoder en [status.rs](https://github.com/herdrdev/herdr-core/blob/master/crates/herdr-core/src/status.rs) (**verificar el parser antes de implementar**):

```text
ESC ] 7501 ; state=<s>[:id=<id>][:kind=<k>][:progress=<0-100>][:app=<name>][:title=<b64>][:msg=<b64>] ESC \
```

`state`: `idle | working | done | blocked | error` (`clear` borra el registro y sus hijos); `kind` (`permission | question | auth`) solo con `blocked`; `title`/`msg` en base64; sin `id` es el registro raíz, con `id` (`build`, `build/test`) son hijos. El turno sube cada vez que el raíz sale de `working`. hc *"never infers status from the screen"*.

**Para que el plugin de un agente se integre con Herdr:** en la TUI, un hook del agente que llame a `$HERDR_BIN_PATH pane report-agent`; en hc/app, emitir OSC 7501 por la tty.

---

## 7. Socket API

### 7.1 TUI

NDJSON `{"id", "method", "params"}` → `{"id", "result"}` o `{"id", "error": {code, message}}`. **Sin autenticación.** Schema con `herdr api schema --json` ([socket-api](https://herdr.dev/docs/socket-api/)).

| Grupo | Métodos |
| :--- | :--- |
| server | `ping`, `server.stop`, `server.reload_config`, `server.agent_manifests`, `server.reload_agent_manifests` |
| UI | `notification.show`, `client.window_title.set/clear`, `popup.close` |
| session | `session.snapshot` |
| workspace | `create/list/get/focus/rename/move/move_block/report_metadata/close` |
| worktree | `list/create/open/remove` |
| tab | `create/list/get/focus/rename/move/close` |
| pane | `split/swap/move/zoom/layout/process_info/neighbor/edges/focus_direction/resize/list/current/get/rename/`**`send_text/send_keys/send_input`**`/read/report_agent/report_agent_session/report_metadata/clear_agent_authority/release_agent/close/wait_for_output`, `pane.input.set` |
| layout | `export/apply/set_split_ratio` |
| agent | `list/get/read/explain/`**`send_keys/prompt`**`/wait/rename/focus/start/view.set/view.clear` |
| events | `subscribe/wait` |
| integration | `install/uninstall` |
| plugin | `link/list/unlink/enable/disable/action.list/action.invoke/log.list/pane.open/pane.focus/pane.close` |

Errores: `not_found`, `invalid_params`, `agent_blocked`, `events_lost`, `invalid_resume_argv`, `resume_not_accepted`, `plugin_disabled`, `popup_not_open`, `platform_unsupported`, `workspace_group_close_required`.

### 7.2 herdr-core

JSON-RPC 2.0 (plain o framed; [hc.openrpc.json](https://github.com/herdrdev/herdr-core/blob/master/schema/hc.openrpc.json)): `hello`, `server.*`, `terminal.run/list/get/kill/rm/keep/label/read/attach/send/wait/keys/interrupt/put*`, **`machine.exec`, `file.read`, `file.write`, `file.edit`**, `doc.get/set`, `events.subscribe`, `machine.add/list/connect/rename/rm`, `rpc.discover`. *"There is no separate API password or per-client permission check."*

---

## 8. MCP

**Herdr no expone ni consume MCP.** La única mención (changelog 0.9.0, "Claude Code MCP questions … now stay blocked") es detección en pantalla.

---

## 9. Configuración de la TUI

Referencia: [config-reference](https://herdr.dev/tui/docs/config-reference/).

| Sección | Claves relevantes |
| :--- | :--- |
| `[theme]` | `name`, `auto_switch`, `dark_name`, `light_name`, `custom.*`, `custom.light.*`, `custom.dark.*` |
| `[terminal]` | `default_shell`, `shell_mode`, `new_cwd` (follow \| home \| current \| path), `kitty_graphics` |
| `[keys]` | `prefix = "ctrl+b"`, ~60 acciones, `[[keys.command]]` |
| `[ui]` | sidebar, `mouse_capture`, `tab_bar_right` (incluye `{type = "command", command, interval_seconds, timeout_seconds}`), `window_title`, `status_indicators` |
| `[ui.sidebar.agents/spaces]` | `rows`, `rows_by_agent`, tokens `$name` con `rules` |
| `[ui.toast]`, `[ui.sound]` | Notificaciones y sonido |
| `[session]` | `resume_agents_on_restore`, `startup_per_agent_delay_ms` |
| `[worktrees]` | `directory = "~/.herdr/worktrees"` |
| `[update]` | `channel`, `version_check`, `manifest_check` |
| Otras | `[remote]`, `[advanced]`, `[experimental]`, `[server]` |

Variables: `HERDR_CONFIG_PATH`, `HERDR_SESSION`, `HERDR_SOCKET_PATH`, `HERDR_PROCESS_DETECTION`, `HERDR_LOG`, `HERDR_DISABLE_SOUND`. Config por plugin: archivos libres en `HERDR_PLUGIN_CONFIG_DIR` (Herdr no los valida); estado en `HERDR_PLUGIN_STATE_DIR`.

---

## 10. Mods de Interfaz

### 10.1 TUI

| Mod | Mecanismo |
| :--- | :--- |
| Temas | Predefinidos o `[theme.custom]` |
| Teclas | `[keys]` |
| Panes de plugin | `[[panes]]` |
| Sidebar | Filas de tokens `$token` alimentados por `pane report-metadata` / `workspace report-metadata` (≤ 80 caracteres, con TTL) |
| Statusline | `tab_bar_right` con `type = "command"` |
| Notificaciones | `notification.show` / `herdr notification show` |
| Vistas de agente | `agent.view.set` |

### 10.2 App: Mods JS

Fuentes: [mods.md](https://github.com/herdrdev/herdr-app/blob/master/docs/mods.md), [defaults/AGENTS.md](https://github.com/herdrdev/herdr-app/blob/master/defaults/AGENTS.md).

```text
~/.config/herdr-app/mods/<name>/
├── main.js     clase View de gpui-kit sobre QuickJS
├── link.js     export handleLink(...) -> "handled" | "pass"
└── mod.json    place, size, focusable, link_handlers[{kind, pattern}]
```

- API del módulo `herdr`: datos (`spaces()`, `agents()`, `terminals()`, `focused()`, `theme()`), UI (`run(cmd, args)`, `openOverlay`, `notify`, `setBadge`, `setWindowTitle`), sistema (`exec(cmd, args, {on})`, `hc(args)`, `fetch`, `readTextFile`, `writeTextFile`, `watchFolder`), estado (`shared`/`setShared`, `nextCommand()`), timers (`cx.timer`, `cx.spawn`).
- Herramientas: `herdr-app check <mod>`, `herdr-app status`, `herdr-app shot`.
- `keys.json` con secciones `global`, `layout:<n>`, `terminal`, `mod:<n>`; la acción `send:<text>` escribe en la terminal. Los layouts declaran `link_handlers`. `ctrl-alt-shift-escape` restaura el layout por defecto.
- **Sin sandbox:** *"Mods can run programs, read files and use the network"*.

---

## 11. Segundo Plano y LSP

- `[[startup]]` es de una sola vez y sin supervisión; procesos huérfanos *"are not protected from cleanup"*. Para algo persistente: un pane de plugin o `hc run --bg`. Los mods de la app usan `cx.timer`/`cx.spawn`.
- **LSP: no aplica.**

---

## 12. Distribución

| Producto | Mecanismo |
| :--- | :--- |
| Plugins TUI | `herdr plugin install owner/repo[/subdir] [--ref] [--yes]`, `update`, `uninstall`, `link`, `unlink`, `enable`, `disable`, `list`, `config-dir`, `log list`. Clona con git, muestra un preview con build y startup, construye en un directorio nuevo y registra; las versiones viejas se conservan hasta que sus servidores terminan; `update` vuelve al `--ref` registrado |
| Marketplace TUI | [herdr.dev/plugins](https://herdr.dev/tui/plugins/) indexa repos públicos con topic `herdr-plugin` cada 30 min, **sin revisión** ([marketplace](https://herdr.dev/tui/docs/marketplace/)); ejemplos en `ogulcancelik/herdr-plugin-examples` |
| Mods y layouts (app) | herdr.dev/mods (topic `herdr-mod`) y herdr.dev/layouts (topic `herdr-layout`) con `RECIPE.md` y `preview.png`; se instalan **copiando la carpeta** |
| Seguridad | Sin sandbox ni firma de plugins en ninguno de los tres; updates verificados solo por SHA-256 contra un manifiesto del mismo servicio |

---

## 13. Relación con los Plugins de los Agentes Hospedados

### 13.1 Qué se Conserva

Hooks, settings, plugins y `session_id` de cada agente; `CLAUDE_PROJECT_DIR` (lo define el agente desde donde se lanza); worktrees git reales en `~/.herdr/worktrees/<repo>/<branch-slug>`; marcadores `HERDR_ENV=1`, `HERDR_PANE_ID`, `TERM_PROGRAM=herdr` (TUI) y `HC_TERMINAL_ID` (core). Los panes nuevos ya no heredan marcadores de Claude Code (0.9.2). Herdr no agrega capa de permisos ni sandbox.

### 13.2 Riesgos para Plugins de Seguridad

| # | Vector | Por qué importa |
| :--- | :--- | :--- |
| 1 | `herdr pane run <id> "<cmd>"`, `pane send-text`, `pane send-input` | El comando corre en **la shell de otro pane**; ningún hook del agente que lo pidió lo ve |
| 2 | `agent send-keys`, `pane send-keys` | **Es el mecanismo documentado para responder diálogos bloqueados** (`agent prompt` devuelve `agent_blocked` y la doc indica usar `send-keys`). Un agente puede aprobar el prompt de confirmación de otro. hc: *"blocked can prevent a normal send, but it is not an access boundary"* |
| 3 | `agent start … -- <args>` | Inyecta flags al agente lanzado (`--dangerously-skip-permissions`, `--settings`) |
| 4 | `hc file write\|edit`, `hc exec`, `hc send`, `hc keys`, `hc run` (también `--on <máquina>`) | Escriben archivos y ejecutan comandos **sin pasar por los tools `Write`/`Edit`/`Bash`** del agente, incluso en máquinas remotas |
| 5 | `herdr-app do … --argv/--task`, acción `send:` de `keys.json` | Mismo efecto desde la app |
| 6 | Configuración ejecutable fuera del proyecto | `~/.config/herdr/config.toml` (`tab_bar_right` command), `~/.config/herdr/agent-detection/*.toml`, `~/.config/herdr-app/{mods,layouts,keys.json}`, `~/.config/herdr-core/templates/*.md`, `herdr-plugin.toml` (`[[startup]]` corre en cada arranque sin sandbox) |

### 13.3 Plugin Cross-Agent que Aproveche Herdr

1. En cada agente, un hook propio (`SessionStart`/`Stop`/`Notification` o equivalente) que reporte estado con `$HERDR_BIN_PATH pane report-agent --source <id>` (TUI) u OSC 7501 (hc/app).
2. Un plugin de Herdr con `[[events]] on = "pane.agent_status_changed"` para notificar u orquestar.
3. Opcional: metadata en el sidebar con `report-metadata`.

Para un **guardrail** Herdr no sirve: todo ocurre después del hecho. El control debe seguir en el hook previo de cada agente, y ese hook debe reconocer los vectores de §13.2.

---

## 14. Tabla de Compatibilidad por Componente

| Claude Code | Herdr | ¿Soportado? | Notas |
| :--- | :--- | :---: | :--- |
| Manifiesto | `herdr-plugin.toml` (TUI); `mod.json` (app) | Parcial | TOML propio |
| Skills | — | No | Herdr publica su skill, pero un plugin no empaqueta skills |
| Comandos slash | `[[actions]]` + `[[keys.command]]` | Parcial | Por tecla o CLI, no desde el prompt del agente |
| Subagentes | `agent start`, `hc run` (templates) | Equivalente funcional | Agentes completos en panes |
| Hooks | `[[events]]` (22) | Solo posteriores | Sin block/ask/allow |
| MCP | — | No | |
| LSP | — | No | |
| Monitores | `[[startup]]`, pane de plugin, `events.subscribe` | Parcial | Startup sin supervisión |
| `bin/` | `command` relativo a la raíz | Sí | 0.8.0+ |
| Settings | `config.toml` / `settings.json` | Sí (globales) | El plugin no los inyecta |
| Output styles | — | No | |
| Temas | `[theme.custom]`; layouts de la app | Sí, no empaquetables en plugin TUI | |
| Statusline | `tab_bar_right` command, tokens del sidebar | Sí | |
| `userConfig` | `HERDR_PLUGIN_CONFIG_DIR` | Parcial | Sin schema ni prompt |
| `PLUGIN_ROOT` / `PLUGIN_DATA` | `HERDR_PLUGIN_ROOT` / `HERDR_PLUGIN_STATE_DIR` | Sí | No guardar estado en la raíz |
| Marketplace | Topics `herdr-plugin`, `herdr-mod`, `herdr-layout` | Sí | Sin revisión |
| Scopes | Solo usuario global | No | Sin scope de proyecto |

---

## 15. No Verificado

- Parser de entrada de OSC 7501 (solo se leyó el encoder y los tests).
- Si Claude Code, Codex u otros agentes emiten OSC 7501 de forma nativa.
- Ruta exacta de `state_dir` (y de `HERDR_PLUGIN_STATE_DIR`).
- Versión en que aparecieron los plugins de la TUI (probablemente 0.6.10–0.7.0).
- Paleta de comandos de la TUI con acciones de plugins.
- Payload completo de cada evento (requiere `herdr api schema --json`).
- Métodos del socket de control de la app y sus permisos.
- Discrepancia de versión de la integración de Claude (doc v6, script 11).
