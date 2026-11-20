# Propuesta de Arquitectura: Núcleo Neutral + Adaptadores por Host

> Diseño para que Synapse proteja Codex, Copilot, Cursor, Antigravity, OpenCode (y opcionalmente Gemini CLI) sin duplicar la lógica de decisión, y para cerrar el bypass de Herdr. Se apoya en los hallazgos de los documentos 02–08.

---

## 1. Principios

1. **La lógica de decisión no cambia.** Las reglas deterministas, Laya y la traza se quedan como están. Solo cambian la entrada y la salida.
2. **El host se declara, no se adivina.** Cada manifiesto pasa `--host <nombre>` al script. La autodetección por forma del payload queda como respaldo (por ejemplo, para el import de plugins Claude que hace Cursor).
3. **Degradar hacia el lado seguro.** Cuando un host no soporta `ask`, la decisión se convierte en `deny` con un motivo que le pide al agente consultar al usuario. Nunca en `allow` silencioso.
4. **Ningún host debe fallar abierto por formato.** Si un host ignora `exit 2` (Antigravity) o rechaza `allow` (Codex), el adaptador emite lo que ese host sí respeta.
5. **Un archivo por host solo donde las rutas chocan.** Reutilizar `hooks/hooks.json` en todos los hosts que lo aceptan tal cual.

---

## 2. Vista General

```text
                       +------------------------------------------------------+
  stdin (forma host)   |  hooks/<hook>.py --host H                            |
  ───────────────────▶ |                                                      |
                       |  hosts.load(H).normalize(raw, env) ──▶ HookEvent     |
                       |      ├─ tool_kind: shell | read | write | edit       |
                       |      ├─ command, files[{path, new_text, old_text}]   |
                       |      ├─ cwd, project_dir, session_id, host           |
                       |      └─ patch parser (apply_patch / patchText)       |
                       |                                                      |
                       |  lógica actual del hook (sin cambios)                |
                       |      └─▶ Decision{block|ask|allow|pass, rule,        |
                       |                   reason, context, decided_by}       |
                       |                                                      |
                       |  hosts.load(H).emit(Decision) ──▶ exit code + I/O    |
  ◀─────────────────── |      └─ degrada ask/allow/context según capacidades  |
  exit / stdout / err  +------------------------------------------------------+
```

---

## 3. El Contrato Interno

### 3.1 `HookEvent`

| Campo | Tipo | Origen por host |
| :--- | :--- | :--- |
| `host` | `str` | `--host` o autodetección |
| `tool_kind` | `shell \| read \| write \| edit` | Tabla de nombres de tool por host (§3.3) |
| `command` | `str \| None` | `tool_input.command`, `toolCall.args.CommandLine`, `ev.input.command` |
| `files` | `list[FileChange]` | Uno por llamada en Claude; **varios** en `apply_patch` |
| `cwd` | `str` | `cwd`, `Cwd`, `workdir`, `dir_path`; vacío → `project_dir` |
| `project_dir` | `str` | Cadena de §5 |
| `session_id` | `str` | `session_id`, `conversationId`, `conversation_id`, `sessionID` |

`FileChange = {path, new_text | None, old_text | None, op: add | update | delete | move}`.

### 3.2 `Decision`

Ya existe implícitamente en las firmas de `emit_decision`/`emit_block` (`decision`, `reason`, `decided_by`, `rule`, `evidence`, `context`). Se formaliza como valor de retorno para que el adaptador la traduzca.

### 3.3 Tabla de Nombres de Tool

| Host | shell | read | write | edit |
| :--- | :--- | :--- | :--- | :--- |
| Claude Code | `Bash` | `Read` | `Write` | `Edit` |
| Codex | `Bash` | — (MCP `mcp__*`) | `apply_patch` | `apply_patch` |
| Copilot (`PreToolUse`) | `Bash` | `Read` | `Write` | `Edit` (incluye `apply_patch`) |
| Cursor | `Shell` / evento `beforeShellExecution` | evento `beforeReadFile` | `Write` | `Write` |
| Antigravity | `run_command` | `view_file` | `write_to_file` | `replace_file_content`, `multi_replace_file_content` |
| OpenCode V2 / V1 | `shell` / `bash` | `read` | `write` | `edit`, `patch` / `apply_patch` |
| Gemini CLI | `run_shell_command` | `read_file`, `read_many_files` | `write_file` | `replace` |

### 3.4 Tabla de Campos

| Host | ruta | contenido nuevo | contenido previo |
| :--- | :--- | :--- | :--- |
| Claude Code | `file_path` | `content` / `new_string` | `old_string` |
| Codex | sobre del patch | líneas `+` / cuerpo de `Add File` | líneas `-` |
| Copilot | `path` (por confirmar) | `file_text` / `new_str` (por confirmar) | `old_str` |
| Cursor | `file_path` | `content` (por confirmar) | — |
| Antigravity | `TargetFile` / `AbsolutePath` | `CodeContent` / `ReplacementContent` / `ReplacementChunks[*]` | `TargetContent` |
| OpenCode V2 / V1 | `path` / `filePath` | `content` / `newString` / `patchText` | `oldString` |
| Gemini CLI | `file_path` | `content` / `new_string` | `old_string` |

---

## 4. Matriz de Emisión

| Host | `block` | `ask` | `allow` | `context` |
| :--- | :--- | :--- | :--- | :--- |
| **Claude Code** | `exit 2` + stderr | `hookSpecificOutput.permissionDecision:"ask"` | `permissionDecision:"allow"` | `additionalContext` |
| **Codex** | `exit 0` + `permissionDecision:"deny"` (más robusto que `exit 2`) | ⚠️ → `deny` + motivo "requiere confirmación del usuario" | `exit 0` sin stdout | `additionalContext` (≤2500 tokens) |
| **Copilot** | `exit 2` + stderr (o `permissionDecision:"deny"` plano) | `permissionDecision:"ask"` plano (se vuelve `deny` en autopilot/nube) | `{}` o `permissionDecision:"allow"` plano | No documentado → se pierde |
| **Cursor** | `exit 2` o `permission:"deny"` | `permission:"ask"` en shell; ⚠️ → `deny` en `preToolUse` / `beforeReadFile` | `permission:"allow"` | `agent_message` |
| **Antigravity** | ⚠️ **`exit 0`** + `{"decision":"deny"}` | `ask` / `force_ask` | `allow` (pasa) o `auto_approve` | Solo en `reason` |
| **OpenCode** (shim) | `throw new Error(reason)` | `ctx.permission.assert(...)` (V2); ⚠️ → `throw` en V1 | `permission.hook("evaluate")` | Se pierde |
| **Gemini CLI** | `exit 2` + stderr | ⚠️ → `deny` + motivo | `exit 0` / `{"decision":"allow"}` | Se pierde |

### 4.1 Política de Degradación de `ask`

Variable nueva `SYNAPSE_ASK_FALLBACK` = `deny` (por defecto) | `allow`.

- `deny`: el tool no corre y el motivo dice *"Synapse requiere tu confirmación explícita para esto: <motivo>. Pide al usuario que lo apruebe y reintenta"*. El agente le traslada la pregunta al usuario, que es el comportamiento más cercano a `ask`.
- `allow`: solo para quien prefiera no ser interrumpido. La decisión queda en la traza como `ask_degraded`.

La traza registra siempre la decisión original y la emitida (`decision: ask`, `emitted: deny`) para medir cuánto se degrada por host.

### 4.2 Fallo Interno

Hoy `run_main` registra el error y lo relanza (Claude Code trata un código distinto de 0 y de 2 como error no bloqueante). Se mantiene esa paridad en todos los hosts: un crash de Synapse no bloquea. Si se quisiera fail-closed, Cursor (`failClosed: true`) y Copilot (no-cero = deny) lo permiten de forma nativa; Antigravity exigiría capturar la excepción y emitir `deny`. Es una decisión de producto, no técnica.

---

## 5. Rutas Neutrales

| Concepto | Resolución propuesta (en orden) |
| :--- | :--- |
| Raíz del proyecto | `CLAUDE_PROJECT_DIR` → `CURSOR_PROJECT_DIR` → `GEMINI_PROJECT_DIR` → `workspacePaths[0]` / `workspace_roots[0]` → `ctx.location.project.directory` (shim) → `cwd` |
| Raíz del plugin | `CLAUDE_PLUGIN_ROOT` → `PLUGIN_ROOT` → `CURSOR_PLUGIN_ROOT` → `Path(__file__).resolve().parents[1]` |
| Logs | `SYNAPSE_LOG_DIR` → `<config del host>/logs/synapse` (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `~/.gemini`, `~/.cursor`, `COPILOT_HOME`) |
| Memoria como raíz permitida | Solo en Claude Code (concepto propio) |
| Config por proyecto | Mantener `.claude/synapse-*.json` y aceptar `.synapse/*.json` como nombre neutral |

> [!NOTE]
> `Path(__file__)` como último recurso hace que el plugin root deje de depender de cualquier variable del host. Es el cambio que más hosts desbloquea a la vez (Copilot CLI, Antigravity, el shim de OpenCode).

---

## 6. Parser de Patches

`apply_patch` aparece en Codex, OpenCode (`patch`/`apply_patch`), Copilot (`apply_patch` → `Edit`) y Factory. Un único módulo `hooks/patch.py`:

```text
entrada: texto entre "*** Begin Patch" y "*** End Patch"
salida : list[FileChange]
  *** Add File: p        -> op=add,    path=p, new_text=<cuerpo sin el '+' inicial>
  *** Update File: p     -> op=update, path=p, new_text=<líneas '+'>, old_text=<líneas '-'>
     *** Move to: q      -> op=move,   path=p, dest=q  (protect_files evalúa p y q)
  *** Delete File: p     -> op=delete, path=p
```

- `detect_secrets` escanea `new_text` de cada `FileChange`.
- `protect_files` evalúa cada `path` (y `dest`). Un solo archivo protegido rechaza el patch completo; el motivo debe nombrar qué archivo lo causó.
- Un patch malformado no se interpreta como seguro: se evalúa el texto completo como contenido (para secretos) y se registra `patch.parse_error`.

---

## 7. Empaquetado por Host

```text
claude-plugin-synapse/
├── .claude-plugin/plugin.json        Claude Code · Codex (compat) · Copilot (legado) · VS Code
├── hooks/hooks.json                  Claude Code · Codex · Copilot (legado)  ← mismo archivo
├── .antigravity-plugin/plugin.json   Antigravity (additionalProperties:false)
├── hooks.json                        Antigravity (raíz, indexado por nombre de hook)
├── .cursor-plugin/plugin.json        Cursor
├── adapters/
│   ├── cursor/hooks.json             Cursor (eventos propios; ver nota)
│   ├── gemini/                       Gemini CLI (gemini-extension.json + hooks/hooks.json)
│   └── opencode/                     Paquete npm: shim TS (V1 server() + V2 setup())
├── hooks/*.py                        Núcleo + hosts.py + patch.py
├── rules/security-guardrails.md      + frontmatter `trigger: always_on` (Antigravity)
└── skills/synapse-status/SKILL.md    Portable
```

| Host | ¿Comparte `hooks/hooks.json`? | Motivo |
| :--- | :--- | :--- |
| Claude Code | Sí | Origen |
| Codex | **Sí** | Mismo formato; matchers `Bash` y `^(Edit\|Write)$` válidos por alias; `CLAUDE_PLUGIN_ROOT` disponible. `^Read$` no matchea nada (inofensivo). El host se autodetecta por `turn_id`. |
| Copilot | **Sí** (con evento `PreToolUse` PascalCase) | Nombres de tool Claude. Riesgo: `CLAUDE_PLUGIN_ROOT` no documentado en hooks del CLI → el comando necesita un fallback de ruta. |
| Cursor | No | Eventos distintos (`beforeShellExecution`, `beforeReadFile`, `preToolUse`). Verificar si `.cursor-plugin/plugin.json` acepta una ruta de hooks personalizada; si no, el plugin de Cursor debe publicarse desde `adapters/cursor/` como raíz. |
| Antigravity | No, pero sin choque | Usa `hooks.json` en la raíz. |
| Gemini CLI | No | Mismo path, eventos distintos: requiere raíz propia. |
| OpenCode | No | Plugin TS. |

> [!WARNING]
> Compartir `hooks/hooks.json` con Codex y Copilot tiene una consecuencia: el comando no puede llevar `--host` fijo. En ese archivo el host se resuelve por autodetección (`turn_id` → Codex; `timestamp` ISO + ausencia de `permission_mode` → Copilot; resto → Claude). Los demás hosts sí declaran `--host`.

---

## 8. Herdr: Desenvolver Comandos Indirectos

No requiere adaptador de I/O, solo reglas nuevas en `block_dangerous` (detalle en [`08-herdr.md`](./08-herdr.md#51-ejecución-indirecta-brecha-principal)):

- `herdr pane run <id> "<cmd>"` y `herdr agent start ... -- <cmd>` se analizan como `bash -c "<cmd>"`.
- `send-text`, `send-keys`, `agent prompt`, `plugin install` y escrituras al socket → `ask` con regla `structure.herdr_indirect`.
- Lecturas (`agent list`, `pane read`, `wait`) → sin cambios.
- `is_dev_routine` excluye explícitamente `herdr pane run` y `herdr agent start`.
- `protect_files` agrega `~/.config/herdr/config.toml` y `herdr-plugin.toml`.

El mismo patrón aplica a otros "lanzadores" que deberían tratarse igual: `tmux send-keys`, `screen -X stuff`, `codex exec`, `claude -p`, `gemini -p`, `opencode run`. Es una generalización natural: **cualquier comando que entregue texto a otra shell o a otro agente se desenvuelve o se confirma**.

---

## 9. Defensa en Profundidad por Host

Los hooks de Synapse no son el único control disponible. El README de cada adaptador debería recomendar la capa nativa que cubre lo que el hook pierde:

| Host | Capa nativa recomendada | Cubre |
| :--- | :--- | :--- |
| Codex | Sandbox `workspace-write`; perfil de permisos `"**/*.env"="deny"`; `.rules` con `prompt` para `git push --force` y `git reset --hard` | `ask` perdido; lecturas sin tool `Read`; `.git` |
| Antigravity | Sandbox activo; `permissions.deny` (`read_file(**/.env)`, `command(rm -rf)`); strict mode en IDE | IDE/App donde los hooks podrían no dispararse |
| Gemini CLI | `policies/*.toml` con `ask_user` | `ask` perdido |
| OpenCode | `permission.read["*.env"]="deny"`, `external_directory="ask"` | Respaldo estático |
| Copilot | `.github/hooks/` versionado para el coding agent | Nube, donde los plugins no aplican |
