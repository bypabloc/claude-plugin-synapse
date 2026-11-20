# Herdr

> Herdr (`herdrdev/herdr`, Rust, Apache-2.0, `0.9.5` del 2026-10-14) es un **multiplexor de terminal para agentes**, no un agente. No ejecuta tools: corre Claude Code, Codex, OpenCode, Cursor Agent, Copilot CLI, Antigravity CLI y otros dentro de paneles de terminal reales. **Synapse funciona sin cambios dentro de un panel de Claude Code.** El trabajo real es cerrar un bypass: un agente puede ordenar comandos a otro panel sin pasar por sus hooks.

---

## 1. Qué es Herdr

```text
+-------------------------------- Herdr TUI / App ---------------------------------+
|  sidebar: estado por agente (working | blocked | done | idle)                     |
|                                                                                  |
|  +-------------------------+  +-------------------------+  +-------------------+ |
|  | pane 1: claude          |  | pane 2: codex           |  | pane 3: zsh       | |
|  |  PreToolUse -> Synapse  |  |  hooks propios de Codex |  |  sin guardrails   | |
|  +-----------+-------------+  +-------------------------+  +---------^---------+ |
|              |                                                       |           |
|              |  herdr pane run 3 "rm -rf ..."   (Bash tool de Claude) |           |
|              +-------------------------------------------------------+           |
|                       el comando interno corre en el pane 3:                     |
|                       NINGÚN hook de Synapse lo ve                               |
+----------------------------------------------------------------------------------+
     socket: ~/.config/herdr/herdr.sock (NDJSON)    worktrees: ~/.herdr/worktrees/
```

- Workspaces, tabs y panes; sesiones persistentes vía servidor en segundo plano.
- Los agentes pueden crear panes y dar instrucciones o esperar a otros agentes, por CLI y por socket.
- A fines de 2026 apareció una app de escritorio sobre un runtime nuevo, `herdr-core` (`hc`); la versión de terminal pasó a llamarse "Herdr TUI" y sigue mantenida. `herdr-core` obtiene el estado del agente por un protocolo de secuencias de escape (OSC 7501), sin leer la pantalla. Su README no documenta hooks ni plugins.

Fuentes: [repo](https://github.com/herdrdev/herdr), [docs](https://herdr.dev/docs/), [changelog](https://raw.githubusercontent.com/herdrdev/herdr/master/CHANGELOG.md), [herdr-core](https://github.com/herdrdev/herdr-core), [Thoughtworks Radar](https://www.thoughtworks.com/radar/tools/herdr). Otros repos "herdr" (fullerzz, ttbug, ayamir, naaive) parecen forks o espejos.

---

## 2. Puntos de Extensión de Herdr

**Ninguno puede bloquear, preguntar ni aprobar una llamada a un tool.**

| Mecanismo | Qué hace | ¿Sirve como guardrail? |
| :--- | :--- | :--- |
| **Integrations** (`herdr integration install <agent>`) | Escribe un hook en la configuración del agente. Para Claude Code: `~/.claude/hooks/herdr-agent-state.sh` (respeta `CLAUDE_CONFIG_DIR`) + entrada **SessionStart** en `settings.json`. Reporta el session ID para reanudar sesiones. | No: solo reporta. [Fuente](https://herdr.dev/docs/integrations/) |
| **Plugins** (`herdr-plugin.toml`) | Secciones `[[build]]`, `[[startup]]`, `[[actions]]`, `[[events]]` (p. ej. `on = "worktree.created"`), `[[panes]]`, `[[link_handlers]]`, `[[keys.command]]`. Comandos en cualquier lenguaje, ejecutados como argv. Variables: `HERDR_SOCKET_PATH`, `HERDR_BIN_PATH`, `HERDR_ENV=1`, `HERDR_PLUGIN_ID/ROOT/CONFIG_DIR/STATE_DIR`, `HERDR_PLUGIN_CONTEXT_JSON`, `HERDR_PLUGIN_EVENT(_JSON)`, `HERDR_WORKSPACE_ID/TAB_ID/PANE_ID`. | No: sin protocolo de salida, solo log. **Sin sandbox ni revisión.** [Fuente](https://herdr.dev/docs/plugins/) |
| **Socket API** (NDJSON sobre `~/.config/herdr/herdr.sock`) | Eventos posteriores (`events.subscribe`); envío de entrada: `pane.send_text`, `pane.send_keys`, `agent.send_keys`, `agent.prompt`. | No: sin método approve/deny. `agent.prompt` se niega si el agente está bloqueado (`agent_blocked`). [Fuente](https://herdr.dev/docs/socket-api/) |

---

## 3. Agentes Ejecutados por Herdr

| Pregunta | Respuesta |
| :--- | :--- |
| ¿Corren los hooks nativos del agente? | **Sí.** Un pane es "a real terminal"; Herdr "doesn't wrap or replace agents". Claude Code carga settings, plugins y `PreToolUse` como siempre. El SessionStart de Herdr convive con Synapse sin conflicto. |
| ¿Llega `session_id` a Synapse? | Sí, lo pone Claude Code. |
| ¿Agrega capa de permisos, sandbox o flags? | **No.** `agent start` pasa solo lo que va después de `--`. El estado "blocked" significa que Herdr vio un prompt de aprobación en pantalla. [Fuente](https://herdr.dev/docs/agents/) |
| ¿Cambia `CLAUDE_PROJECT_DIR`? | No. Claude Code lo define desde donde se lanza. Los panes nuevos reciben `TERM_PROGRAM=herdr` y ya no heredan marcadores de Claude Code (changelog 0.9.2). `HERDR_ENV=1` indica "dentro de Herdr". |
| ¿Worktrees? | `git worktree add` en `worktrees.directory/<repo>/<branch-slug>`, por defecto `~/.herdr/worktrees`. Claude Code lanzado ahí recibe `CLAUDE_PROJECT_DIR` = ese worktree. **`allowed_roots` de Synapse ya incluye todos los worktrees del repo**, así que las escrituras entre checkout principal y worktree de Herdr se permiten en ambos sentidos. Repos bare soportados desde 0.6.9. [Fuente](https://herdr.dev/tui/docs/config-reference/) |
| ¿Directorio del pane? | `terminal.new_cwd` (`"follow"` por defecto). Synapse no depende de él porque usa `CLAUDE_PROJECT_DIR`. |

---

## 4. Empaquetado

- Formato propio `herdr-plugin.toml`, instalación desde GitHub (`herdr plugin install owner/repo/subdir`), marketplace que indexa repos con el topic `herdr-plugin`.
- No carga plugins, skills ni reglas de Claude Code. Synapse sigue siendo un plugin de Claude Code.
- Herdr publica su propia skill (`skills/herdr/SKILL.md`, `npx skills add herdrdev/herdr --skill herdr`) que **enseña a los agentes a manejar otros panes**: es justamente lo que habilita el bypass.

---

## 5. Brechas y Trabajo

### 5.1 Ejecución Indirecta (brecha principal)

Claude en un pane puede ejecutar vía su tool `Bash`:

```text
herdr pane run <id> "rm -rf ..."
herdr pane send-text <id> "..."
herdr pane send-keys <id> ...
herdr agent start <kind> -- <cmd>
herdr agent prompt <id> "..."
nc -U ~/.config/herdr/herdr.sock      # socket directo
hc run / hc send                      # herdr-core
```

El comando interno corre en **la shell de otro pane**, no en el `Bash` de Claude. Synapse ve solo `herdr pane run ...` y, si el wrapper no se reconoce, lo trata como un comando opaco.

**Propuesta para `block_dangerous`:**

| Subcomando | Tratamiento |
| :--- | :--- |
| `herdr pane run <id> "<cmd>"` | Desenvolver como `bash -c` y analizar `<cmd>` con las mismas reglas |
| `herdr agent start ... -- <cmd>` | Desenvolver los argumentos tras `--` |
| `pane send-text`, `pane send-keys`, `agent send-keys` | `ask` por defecto (texto libre hacia otra shell o agente) |
| `agent prompt` | `ask` (instrucción a otro agente) |
| `plugin install` | `ask` (código sin sandbox que corre en cada arranque) |
| Escrituras al socket (`nc -U ...herdr.sock`, `socat`) | `ask` |
| `herdr agent list`, `pane read`, `wait` | Solo lectura: pasar sin preguntar |

> [!WARNING]
> `is_dev_routine` y los prefijos de `.claude/synapse-commands.json` **no deben** dejar pasar `herdr pane run` ni `herdr agent start` sin analizar el comando interno.

### 5.2 Teclas que Responden Prompts de Synapse

Un agente podría aprobar el `ask` de Synapse en otro pane de Claude enviando teclas (Enter). `agent.prompt` se niega si el agente está bloqueado, pero la documentación no dice si `agent.send_keys` / `pane.send_keys` también. Por eso `send-keys` dirigido a otro agente debe pedir confirmación.

### 5.3 Configuración de Herdr en `protect_files`

Pedir confirmación al editar `~/.config/herdr/config.toml`, cualquier `herdr-plugin.toml` y la carpeta de configuración de plugins. Los plugins corren código sin sandbox en cada arranque del servidor (`[[startup]]`), y cambiar `worktrees.directory` mueve dónde se crean los checkouts. Estas rutas están fuera del proyecto, así que las escrituras por Bash ya se bloquean; aplica sobre todo a `Write`/`Edit`.

### 5.4 Tests y Traza

- E2E con un worktree bajo `~/.herdr/worktrees/<repo>/<slug>` y con `HERDR_ENV=1`.
- Opcional: registrar `HERDR_PANE_ID` / `TERM_PROGRAM=herdr` en la traza para saber de qué pane vino una decisión.

### 5.5 Fuera de Alcance

- Una versión de Synapse como plugin de Herdr: solo podría observar eventos después del hecho.
- Proteger panes de Codex, OpenCode, Cursor o Antigravity: depende de los adaptadores por agente de los documentos 03–07, no de Herdr.

---

## 6. Tabla de Compatibilidad

| Funcionalidad | ¿Soportada? | Notas |
| :--- | :--- | :--- |
| Hook previo dentro de Herdr | No | Plugins y eventos informativos |
| Protocolo block/ask/allow | No | Sin salida estructurada |
| Hooks de Claude Code dentro de un pane | **Sí** | Terminal real |
| `session_id` de Claude llega a Synapse | Sí | |
| Capa de permisos o sandbox propia | No | |
| Modifica `CLAUDE_PROJECT_DIR` | No | |
| Worktrees git reales | Sí | `~/.herdr/worktrees/<repo>/<slug>` |
| Agentes que controlan otros panes | **Sí** | Origen del bypass |
| Marcadores de entorno | Sí | `HERDR_ENV=1`, `TERM_PROGRAM=herdr`, `HERDR_PANE_ID` |
| Empaquetado compatible con Claude | No | TOML propio |

---

## 7. Pendiente de Verificar

- Si existe el comando `herdr claude` (solo lo menciona la página de la skill).
- Si `agent.send_keys` funciona contra un agente bloqueado.
- Lista completa de eventos de plugins y formato de sus datos.
- Versión y fecha de lanzamiento de `herdr-core`.
