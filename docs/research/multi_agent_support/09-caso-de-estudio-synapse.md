# Caso de Estudio: Portar Synapse a Otros Agentes

> Aplicación de la guía a un plugin real: Synapse (guardrails `PreToolUse` para Claude Code, versión `1.6.1`). Inventario de su acoplamiento con Claude Code, riesgo de instalarlo sin adaptar en cada agente, arquitectura propuesta (núcleo neutral + adaptadores), plan por fases y verificaciones pendientes. Usa los hallazgos de los documentos 02–08.

---

## 1. Qué Hace Synapse

Cuatro hooks Python sobre `PreToolUse`:

| Hook | Herramienta | Decide sobre |
| :--- | :--- | :--- |
| `block_dangerous.py` | `Bash` | Comandos destructivos, escrituras fuera del proyecto, exfiltración, escritura de archivos desde Bash |
| `block_env_read.py` | `Read`, `Bash` | Lectura de archivos `.env` |
| `detect_secrets.py` | `Edit`, `Write` | Credenciales en el contenido escrito |
| `protect_files.py` | `Edit`, `Write` | `.git/`, `.venv/`, lockfiles, configuración de agentes |

Motor dual: reglas deterministas + Laya (clasificador neuronal vía daemon en socket Unix). Laya solo escala a `ask`, nunca bloquea. Toda decisión queda en una traza JSONL con atribución `decided_by`/`rule`.

Componentes de plugin que usa: manifiesto, hooks, una skill (`synapse-status`) y una regla markdown. Es un plugin **centrado en hooks**, el componente menos portable del ecosistema (ver [`08-guia-plugin-multi-agente.md`](./08-guia-plugin-multi-agente.md)).

---

## 2. Superficie de Acoplamiento con Claude Code

```text
          Claude Code (harness)                              Synapse (hooks/*.py)
+-----------------------------------+                +--------------------------------------+
| hooks/hooks.json                  |   stdin JSON   | common.read_hook_input()             |
|  PreToolUse                       |--------------->|   tool_name, tool_input, cwd,        |
|   "Bash" "^(Edit|Write)$" "^Read$"|                |   session_id                         |
| env: CLAUDE_PLUGIN_ROOT           |   exit 2 +     | common.emit_block()                  |
|      CLAUDE_PROJECT_DIR           |<---------------+   stderr "🚫 BLOCKED · ..."           |
|      CLAUDE_CONFIG_DIR            |   exit 0 +     | common.emit_decision()               |
| UI: prompt de confirmación (ask)  |<---------------+   {"hookSpecificOutput": {...}}      |
+-----------------------------------+   stdout JSON  +--------------------------------------+
```

### 2.1 Entrada

| Campo | Lo consume | Supuesto que otros agentes rompen |
| :--- | :--- | :--- |
| `tool_name` | Los 4 hooks (`block_dangerous.py:1132` sale con `exit 0` si no es `Bash`) | Cursor-import entrega `Shell`; Antigravity `run_command`; Codex `apply_patch` |
| `tool_input.command` | `block_dangerous`, `block_env_read` | Antigravity `toolCall.args.CommandLine` |
| `tool_input.file_path` | `protect_files`, `block_env_read` | Codex: rutas dentro de un patch; Antigravity `TargetFile`; OpenCode V2 `path`; Copilot `path` (por confirmar) |
| `tool_input.content` / `new_string` | `detect_secrets` | Codex/OpenCode: líneas `+` de un patch; Antigravity `CodeContent`/`ReplacementContent` |
| `cwd` | Todos | Cursor-import lo entrega vacío |
| `session_id` | Traza | Antigravity `conversationId`; Cursor `conversation_id` |

Supuestos implícitos: **un archivo por escritura** (falso con `apply_patch`), **contenido en claro** (falso con patches), **existe una herramienta de lectura** (falso en Codex).

### 2.2 Salida

| Función | Protocolo | Hosts donde falla |
| :--- | :--- | :--- |
| `emit_block` | `exit 2` + stderr | **Antigravity** (exit ≠ 0 se ignora: falla abierto) |
| `emit_decision("ask")` | `hookSpecificOutput.permissionDecision: "ask"` | Codex (hook fallido → tool corre), Gemini CLI (→ allow), Cursor fuera de shell/MCP, Copilot en autopilot/nube (→ deny) |
| `emit_decision("allow")` | `permissionDecision: "allow"` | Codex (rechazado sin `updatedInput`) |
| `context=` | `additionalContext` | Antigravity, Gemini CLI (en `BeforeTool`), Copilot (no documentado) |

### 2.3 Variables y Rutas

| Variable / ruta | Uso en Synapse | Equivalentes |
| :--- | :--- | :--- |
| `CLAUDE_PLUGIN_ROOT` | Comandos de `hooks.json` | Codex `PLUGIN_ROOT` + alias; Cursor `CURSOR_PLUGIN_ROOT`; Antigravity `PLUGIN_ROOT` (≥ 1.5.3) y cwd = raíz; Gemini `${extensionPath}`; Copilot CLI no documentado |
| `CLAUDE_PROJECT_DIR` | Ancla de `common.allowed_roots` | Codex **no existe** (usar `cwd`); Cursor y Gemini alias; Antigravity `workspacePaths[0]` / `cwd` (≥ 1.5.4); OpenCode `ctx.location.project.directory` |
| `CLAUDE_CONFIG_DIR` | Logs, memoria del proyecto | `CODEX_HOME`, `~/.gemini`, `~/.cursor`, `COPILOT_HOME` |
| `.claude/synapse-*.json` | Config por proyecto | Nombre neutral propuesto: `.synapse/` |

### 2.4 Lo que No Está Acoplado

Tokenización y análisis de shell, reglas de alcance git (worktrees, submódulos), firmas y entropía de secretos, daemon de Laya y traza JSONL. **El núcleo de decisión se reutiliza sin cambios**: el trabajo se concentra en `read_hook_input`, `emit_*`, `allowed_roots`, `get_log_dir` y en manifiestos por host.

---

## 3. Riesgo de Instalar Synapse sin Adaptar

| Agente | Qué pasa hoy | Severidad |
| :--- | :--- | :--- |
| **Cursor** (import automático de plugins Claude, activo por defecto y sin opt-out en el CLI) | `tool_name: "Shell"` → `block_dangerous` termina sin evaluar. **Todo pasa.** Bug reconocido por staff el 28 sep 2026, sin fix | **Crítica** |
| **Antigravity** (`agy plugin import claude` convierte `hooks/hooks.json` de PreToolUse) | Los hooks se importan pero **todo `exit 2` se ignora**, y el stdin es camelCase con `toolCall.args`: los scripts no encuentran `tool_name` y terminan con `exit 0` | **Crítica** |
| **Codex** (lee `.claude-plugin` y `hooks/hooks.json` tal cual) | Bloqueos funcionan. **Todo `ask`/`allow` deja el hook fallido y el tool corre.** `detect_secrets`/`protect_files` leen campos vacíos en `apply_patch` y pasan. `block_env_read` no ve lecturas | Alta |
| **Copilot** (lee `.claude-plugin` y `.claude/settings.json`) | `exit 2` funciona. `ask`/`allow` anidados probablemente se ignoran. Si los argumentos nativos no son `file_path`/`content`, los hooks de escritura pasan | Alta |
| **Gemini CLI** | No importa plugins. Migrando a mano: bloqueos OK, `ask` → allow | Media |
| **OpenCode** | No carga hooks de Claude: sin protección (salvo permisos nativos, `*.env` en `ask`) | Sin cobertura |
| **Herdr** | Synapse funciona dentro de panes de Claude Code. **Bypass por `herdr pane run`** | Media |

---

## 4. Arquitectura Propuesta: Núcleo Neutral + Adaptadores

### 4.1 Principios

1. La lógica de decisión no cambia; solo entrada y salida.
2. El host se declara (`--host <nombre>` en cada manifiesto) y la autodetección por forma del payload queda como respaldo.
3. Degradar hacia el lado seguro: sin `ask`, emitir `deny` con un motivo que pida al agente consultar al usuario.
4. Ningún host debe fallar abierto por formato.
5. Compartir `hooks/hooks.json` donde el formato lo permite; archivos propios solo donde las rutas chocan.

```text
  stdin (forma host) ──▶ hosts.load(H).normalize(raw, env) ──▶ HookEvent
                             ├─ tool_kind: shell | read | write | edit
                             ├─ command, files[{path, new_text, old_text, op}]
                             ├─ cwd, project_dir, session_id, host
                             └─ patch.parse() para apply_patch / patchText
                         lógica actual del hook (sin cambios) ──▶ Decision
                         hosts.load(H).emit(Decision) ──▶ exit + stdout/stderr
                             └─ degrada ask / allow / context según capacidades
```

### 4.2 Tablas de Normalización

| Host | shell | read | write | edit |
| :--- | :--- | :--- | :--- | :--- |
| Claude Code | `Bash` | `Read` | `Write` | `Edit` |
| Codex | `Bash` | — (MCP `mcp__*`) | `apply_patch` | `apply_patch` |
| Copilot (`PreToolUse`) | `Bash` | `Read` | `Write` | `Edit` (incluye `apply_patch`) |
| Cursor | `beforeShellExecution` / `Shell` | `beforeReadFile` | `preToolUse` `Write` | `preToolUse` `Write` |
| Antigravity | `run_command` | `view_file` | `write_to_file` | `replace_file_content`, `multi_replace_file_content` |
| OpenCode V2 / V1 | `shell` / `bash` | `read` | `write` | `edit`, `patch` / `apply_patch` |
| Gemini CLI | `run_shell_command` | `read_file`, `read_many_files` | `write_file` | `replace` |

### 4.3 Matriz de Emisión

| Host | `block` | `ask` | `allow` | `context` |
| :--- | :--- | :--- | :--- | :--- |
| Claude Code | `exit 2` + stderr | `permissionDecision: "ask"` | `permissionDecision: "allow"` | `additionalContext` |
| Codex | `exit 0` + `permissionDecision: "deny"` | ⚠️ → `deny` explicado | `exit 0` sin stdout | `additionalContext` |
| Copilot | `exit 2` o `permissionDecision: "deny"` plano | `permissionDecision: "ask"` plano | `{}` | — |
| Cursor | `exit 2` o `permission: "deny"` | `ask` en shell/MCP; ⚠️ → `deny` en `preToolUse`/`beforeReadFile` | `permission: "allow"` | `agent_message` |
| Antigravity | ⚠️ **`exit 0`** + `{"decision": "deny"}` | `ask` / `force_ask` | `allow` / `auto_approve` | En `reason` |
| OpenCode (shim TS) | `throw new Error(reason)` | `ctx.permission.assert(...)` (V2); ⚠️ → `throw` (V1) | `permission.hook("evaluate")` | — |
| Gemini CLI | `exit 2` + stderr | ⚠️ → `deny` explicado | `exit 0` | — |

Variable nueva `SYNAPSE_ASK_FALLBACK=deny|allow` (por defecto `deny`). La traza registra la decisión original y la emitida (`decision: ask`, `emitted: deny`).

### 4.4 Rutas Neutrales

| Concepto | Resolución (en orden) |
| :--- | :--- |
| Raíz del proyecto | `CLAUDE_PROJECT_DIR` → `CURSOR_PROJECT_DIR` → `GEMINI_PROJECT_DIR` → `workspacePaths[0]` / `workspace_roots[0]` → `cwd` |
| Raíz del plugin | `CLAUDE_PLUGIN_ROOT` → `PLUGIN_ROOT` → `CURSOR_PLUGIN_ROOT` → `Path(__file__).resolve().parents[1]` |
| Logs | `SYNAPSE_LOG_DIR` → `<config del host>/logs/synapse` |
| Memoria como raíz permitida | Solo Claude Code |

### 4.5 Parser de Patches (`hooks/patch.py`)

`*** Add File: p` → `op=add` con el cuerpo; `*** Update File: p` → `op=update` con líneas `+`/`-`; `*** Move to: q` → `op=move` (se evalúan `p` y `q`); `*** Delete File: p` → `op=delete`. Un archivo protegido rechaza el patch completo nombrando la causa. Un patch malformado se evalúa como texto completo para secretos (`patch.parse_error`).

### 4.6 Empaquetado

```text
claude-plugin-synapse/
├── .claude-plugin/plugin.json        Claude Code · Codex · Copilot (legado) · VS Code
├── hooks/hooks.json                  Claude Code · Codex · Copilot (legado)  ← compartido
├── .codex-plugin/                    (no necesario: Codex lee .claude-plugin; evitar el formato portable)
├── .antigravity-plugin/plugin.json   Antigravity
├── hooks.json                        Antigravity (raíz, indexado por nombre de hook)
├── .cursor-plugin/plugin.json        Cursor, con "hooks": "./adapters/cursor/hooks.json"
├── adapters/
│   ├── cursor/hooks.json             beforeShellExecution · beforeReadFile · preToolUse(Write), failClosed
│   ├── gemini/                       gemini-extension.json + hooks/hooks.json envuelto en {"hooks":{}}
│   └── opencode/                     paquete npm: server() V1 + setup() V2 → spawn de los .py
├── rules/security-guardrails.md      + frontmatter `trigger: always_on` (Antigravity)
└── skills/synapse-status/SKILL.md
```

- **Cursor** acepta una ruta propia de hooks en el campo `hooks` de su manifiesto: no hace falta otra raíz.
- En `hooks/hooks.json` compartido no hay `--host` fijo: Codex se detecta por `turn_id`, Copilot por `timestamp` ISO sin `permission_mode`, el resto es Claude.
- Antigravity exige `rules/<x>.md` con `trigger` válido o la regla se descarta en silencio; Claude Code ignora ese frontmatter.

### 4.7 Herdr: Desenvolver Comandos Indirectos

Reglas nuevas en `block_dangerous` (sin adaptador de I/O):

| Subcomando | Tratamiento |
| :--- | :--- |
| `herdr pane run <id> "<cmd>"` | Analizar `<cmd>` como `bash -c` |
| `herdr agent start … -- <args>` | `<args>` son **flags del agente**, no un comando: `ask` ante flags de permisos (`--dangerously-skip-permissions`, `--settings`, `--yolo`…) |
| `pane send-text`, `pane send-input`, `pane send-keys`, `agent send-keys`, `agent prompt` | `ask` (regla `structure.herdr_indirect`). `send-keys` es la forma documentada de responder diálogos bloqueados: puede aprobar el `ask` de otro agente |
| `hc file write\|edit`, `hc exec`, `hc send`, `hc keys`, `hc run`, `herdr-app do` | Mismo trato que `pane run` y que una escritura de archivo (aplicar las reglas de `detect_secrets`/`protect_files` cuando se pueda extraer contenido) |
| `herdr plugin install`, escrituras al socket (`nc -U …herdr.sock`) | `ask` |
| `agent list`, `pane read`, `wait` | Sin cambios |

Generalización: cualquier comando que entregue texto a otra shell o agente (`tmux send-keys`, `screen -X stuff`, `codex exec`, `claude -p`, `gemini -p`, `opencode run`) se desenvuelve o se confirma. `is_dev_routine` excluye explícitamente estos wrappers. `protect_files` agrega la configuración ejecutable de Herdr (§4.8).

### 4.8 Archivos de Gobierno por Host para `protect_files`

| Host | Archivos que piden confirmación al editarse |
| :--- | :--- |
| Codex | `.codex/config.toml`, `.codex/hooks.json`, `.codex/rules/*.rules`, `.codex/agents/*.toml`, `AGENTS.override.md`, `.agents/plugins/marketplace.json` |
| Antigravity | `.agents/hooks.json`, `.agents/mcp_config.json`, `.agents/plugins.json`, `~/.gemini/antigravity-cli/settings.json`, `~/.gemini/config/config.json` (sidecars) |
| Gemini CLI | `.gemini/settings.json`, `.gemini/policies/*.toml` |
| OpenCode | `opencode.json(c)`, `.opencode/plugins/**`, `.opencode/tools/**` |
| Cursor | `.cursor/hooks.json`, `.cursor/rules/**`, `.cursor/mcp.json` |
| Copilot | `.github/hooks/*.json`, `.github/copilot/settings*.json` |
| Herdr | `~/.config/herdr/config.toml`, `~/.config/herdr/agent-detection/*.toml`, `herdr-plugin.toml`, `~/.config/herdr-app/{mods,layouts,keys.json}`, `~/.config/herdr-core/templates/*.md` |

### 4.9 Defensa en Profundidad

| Host | Capa nativa recomendada |
| :--- | :--- |
| Codex | Sandbox `workspace-write`; perfil de permisos `"**/*.env" = "deny"`; `.rules` con `prompt` para `git push --force` y `git reset --hard` |
| Antigravity | Sandbox activo; `permissions.deny` (`read_file(**/.env)`, `command(rm -rf)`); strict mode |
| Gemini CLI | `policies/*.toml` con `ask_user` |
| OpenCode | `permission.read["*.env"] = "deny"`, `external_directory = "ask"` |
| Copilot | `.github/hooks/` versionado para el coding agent en la nube |
| Cursor | Hooks en `~/.cursor/hooks.json` con `failClosed: true` mientras siga el bug del import |

---

## 5. Plan por Fases

```text
 F0  Endurecimiento inmediato  ── cierra la falla abierta del import de Cursor
 F1  Núcleo neutral            ── hosts.py + patch.py + rutas + host en la traza
 ├── F2  Codex       (comparte hooks/hooks.json)
 ├── F3  Copilot     (comparte hooks/hooks.json)
 ├── F4  Herdr       (reglas en block_dangerous)
 F5  Cursor          F6  Antigravity          F7  OpenCode (shim TS)          F8  Gemini CLI (opcional)
```

| Fase | Alcance | Criterio de cierre |
| :--- | :--- | :--- |
| **F0** | `Shell` como alias de `Bash`; `cwd` vacío → raíz del proyecto; plugin root desde `__file__` | Casos con payload estilo Cursor-import; suite verde |
| **F1** | `hosts.py`, `patch.py`, rutas neutrales, `host`/`emitted` en la traza, `SYNAPSE_ASK_FALLBACK` | Cero cambios de decisión en `tests/cases/*.json` para Claude; `pytest` y `--laya gpu` verdes |
| **F2** | Codex: emisión `deny` JSON, `allow` vacío, `ask` degradado; matcher `mcp__.*`; archivos `.codex/**`; regla como skill | E2E contra `codex` ≥ 0.125 |
| **F3** | Copilot: salida plana, mapeo de argumentos, `timeoutSec` | E2E contra `copilot` CLI |
| **F4** | Herdr y wrappers equivalentes | Casos en `tests/cases/block_dangerous.json` |
| **F5** | `.cursor-plugin/plugin.json` con ruta de hooks propia, `failClosed`, regla `.mdc` | E2E en IDE y CLI |
| **F6** | `.antigravity-plugin/plugin.json`, `hooks.json` raíz, emisión siempre `exit 0` | `agy plugin validate --strict`; E2E en `agy` |
| **F7** | `adapters/opencode` con `setup()` V2 (y `server()` V1 si se decide) | Prueba en OpenCode 2.x |
| **F8** | Extensión Gemini | Opcional |

**Orden:** F0 primero porque corrige una exposición existente; Codex y Copilot antes porque reutilizan manifiesto y hooks; Herdr en paralelo porque beneficia a Claude Code hoy; Antigravity después por la duda sobre el IDE; OpenCode al final por exigir TS y npm.

### 5.1 Estrategia de Tests

- **Matriz de equivalencia:** a partir de cada caso canónico de `tests/cases/<hook>.json`, un generador sintetiza el payload de cada host (`tests/hosts/<host>.json` con los mapeos) y verifica que la decisión interna sea idéntica y que la emisión respete el contrato del host.
- **Casos exclusivos:** `apply_patch` multi-archivo, lecturas MCP en Codex, `read_many_files` en Gemini, `ReplacementChunks` en Antigravity, payload de Cursor-import.
- **Contrato de emisión:** validar contra los schemas publicados (Codex: `codex-rs/hooks/schema/generated`, `additionalProperties: false`).
- **E2E reales** marcados `pytest.mark.<host>`, omitidos si el binario no está.
- **Captura de payloads:** un hook "sonda" por host copia stdin y entorno filtrado a `./tmp/probe/<host>/`; los payloads sanitizados se versionan como fixtures.

---

## 6. Verificaciones Pendientes

| # | Host | Pregunta | Impacto |
| :--- | :--- | :--- | :--- |
| 1 | Codex | ¿unified exec dispara siempre `PreToolUse` `Bash`? ¿llega `workdir`? | Comandos sin inspeccionar / cwd incorrecto |
| 2 | Codex | ¿`additionalContext` sin `permissionDecision`? | Se pierde `git_recoverable_delete` |
| 3 | Codex | ¿Hooks en formato portable? (el loader devuelve vacío) | Usar manifiesto legacy (ya propuesto) |
| 4 | Copilot | Argumentos nativos de `view`, `create`, `edit`, `str_replace_editor` | Hooks de escritura ciegos |
| 5 | Copilot | ¿Ignora `hookSpecificOutput`? ¿`CLAUDE_PLUGIN_ROOT` en hooks del CLI? | `ask`/`allow` perdidos; script no encontrado |
| 6 | Cursor | `tool_input` real de `Write` en `preToolUse` | `detect_secrets` ciego |
| 7 | Cursor | Estado del bug del import | Define urgencia de F0 |
| 8 | Antigravity | ¿Hooks en el IDE standalone? | Cobertura solo App/CLI |
| 9 | Antigravity | Campos de `ReplacementChunks`; timeout de hooks | Multi-edit sin escanear |
| 10 | Antigravity | ¿`agy plugin import claude` traduce algo del contrato de scripts? | Podría agravar la falla abierta |
| 11 | OpenCode | ¿V1 `permission.ask` se arregló? Latencia de `spawn` de Python | `ask` en V1; experiencia |
| 12 | Gemini CLI | ¿`decision: "ask"` en runtime? | `ask` perdido |
| 13 | Herdr | Parser de OSC 7501 y payload completo de eventos | Solo relevante para integración de estado, no para guardrails |

### 6.1 Resueltas en la Segunda Ronda

- **Cursor:** los hooks de plugins sí corren en el CLI (desde `v2026.08.11`); el manifiesto acepta una ruta de hooks propia.
- **Antigravity:** el stdin incluye `cwd` (CLI ≥ 1.5.4) y los hooks reciben `PLUGIN_ROOT`/`PLUGIN_DATA` como variables (≥ 1.5.3); claves desconocidas del manifiesto son warning, no error.
- **Gemini CLI:** el `hooks/hooks.json` de una extensión va envuelto en `{"hooks": {...}}`.
- **Codex:** la extensión de IDE no carga plugins; el manifiesto portable no carga hooks en `main`.
- **Herdr:** `agent send-keys` sí funciona contra un agente bloqueado (es el camino documentado); `agent start -- <args>` pasa flags al agente, no un comando; herdr-core expone escritura de archivos y ejecución remota por CLI y socket.

### 6.2 Riesgos Transversales

- **Confianza por hash (Codex) y huella (Gemini):** cada release obliga a re-aprobar los hooks.
- **Directorio público de Codex no admite plugins con hooks:** distribuir por marketplace propio (`bypabloc/claude-plugins` ya usa `.claude-plugin/marketplace.json`, que Codex y Copilot leen; las entradas con fuente `github` deben pasar a `url`/`git-subdir` para Codex).
- **Timeouts:** Copilot falla abierto por timeout (30 s); Antigravity 30 s; Gemini 60 s; Codex 600 s. Declarar timeouts explícitos.
- **Deriva de APIs:** Antigravity, OpenCode V2 y Cursor cambiaron mucho en 2026; fijar versiones mínimas por adaptador.
