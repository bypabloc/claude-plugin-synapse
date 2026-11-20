# OpenAI Codex CLI

> Hooks, plugins, tools y mecanismos complementarios de Codex (CLI `0.171.0` al 2026-11-19) evaluados contra los 4 hooks de Synapse. Codex es el host **más parecido a Claude Code**: `hooks.json` casi idéntico y alias `CLAUDE_PLUGIN_ROOT`. Las diferencias que obligan a escribir un adaptador son tres: no soporta `ask`, las ediciones llegan como `apply_patch` y no existe una herramienta `Read`.

---

## 1. Sistema de Hooks

### 1.1 Eventos

`SessionStart`, `SessionEnd`, `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, **`PreToolUse`**, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `PreModelRequest`, `PostModelRequest`, `Interrupt`.

### 1.2 Ubicaciones de Configuración

| Nivel | Ruta | Nota |
| :--- | :--- | :--- |
| Sistema | `/etc/codex/hooks.json` | |
| Usuario | `$CODEX_HOME/hooks.json` (`~/.codex/hooks.json`) | |
| Proyecto | `.codex/hooks.json` | Solo si el proyecto es de confianza |
| Inline | tablas `[hooks]` en `config.toml` | |
| Plugin | `hooks/hooks.json` en la raíz del plugin, o entrada `hooks` del manifiesto | |
| Administrado | `[hooks]` en `requirements.toml` | No requiere revisión de confianza |

### 1.3 Formato

Misma forma que Claude Code:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 \"${PLUGIN_ROOT}/hooks/block_dangerous.py\"", "timeout": 600, "statusMessage": "Synapse" }
        ]
      }
    ]
  }
}
```

Campos extra: `type: "mcp_tool"` (el handler es un tool de un servidor MCP conectado), `async`, `additionalContextLimit` (~2500 tokens por defecto), `commandWindows`. El comando corre con `$SHELL -lc` (o `/bin/sh -lc`) en el `cwd` de la sesión.

### 1.4 Matchers

Regex sobre el nombre del tool. `*`, `""` o la ausencia de matcher equivalen a "todos". **`apply_patch` también matchea con `Edit` y `Write`** (alias), así que el matcher `^(Edit|Write)$` de Synapse sirve sin cambios.

### 1.5 Entrada (`stdin`) en `PreToolUse`

| Campo | Requerido | Nota |
| :--- | :--- | :--- |
| `session_id` | Sí | |
| `transcript_path` | Sí (puede ser `null`) | |
| `cwd` | Sí | |
| `hook_event_name` | Sí | |
| `model`, `permission_mode`, `turn_id` | Sí | `turn_id` sirve para detectar el host |
| `tool_name`, `tool_use_id`, `tool_input` | Sí | |
| `agent_id`, `agent_type` | No | |

El schema declara `additionalProperties: false`.

### 1.6 Salida y Códigos de Salida

| Intención | Cómo | Estado |
| :--- | :--- | :--- |
| **Bloquear** | `hookSpecificOutput.permissionDecision: "deny"` + `permissionDecisionReason` no vacío | Recomendado |
| Bloquear (alternativa) | `exit 2` + stderr, o el formato antiguo `{"decision":"block","reason":...}` | Documentado; un reporte de terceros indica que en Codex 0.144 para Windows falló abierto |
| **Permitir** | `exit 0` sin stdout | Único `allow` válido sin reescritura |
| `allow` explícito | `permissionDecision: "allow"` | **Solo válido junto a `updatedInput`**. Sin él: `"PreToolUse hook returned unsupported permissionDecision:allow"`, el hook queda fallido y el tool corre igual |
| **Preguntar (`ask`)** | — | **No soportado.** Se parsea, el hook queda fallido y el tool corre: **falla abierto** |
| Reescribir input | `permissionDecision: "allow"` + `updatedInput` | Soportado (`command` como string para Bash y `apply_patch`) |
| Contexto al modelo | `hookSpecificOutput.additionalContext` | Soportado. Devolverlo sin `permissionDecision` está inferido del parser, no verificado |
| Rechazados | `continue:false`, `stopReason`, `suppressOutput`, `decision:"approve"` | |

> [!CAUTION]
> La salida actual de `emit_decision("allow")` y `emit_decision("ask")` de Synapse **produce hooks fallidos en Codex** y el tool se ejecuta. Un Synapse sin adaptador en Codex pierde silenciosamente todas las confirmaciones (force push, `reset --hard`, borrados permanentes, `.git`, sospechas de Laya).

### 1.7 `PermissionRequest`

Corre solo cuando Codex **ya iba** a pedir aprobación. Responde `decision.behavior` con `allow` (salta el prompt) o `deny`. Sin respuesta, sigue el flujo normal. Sirve para auto-aprobar, no para crear un prompt nuevo.

### 1.8 Activación y Confianza

- Activos por defecto. Se apagan con `[features] hooks = false` (`codex_hooks` es alias obsoleto).
- **Todo hook no administrado (usuario, proyecto, plugin) se omite hasta revisarlo con `/hooks`.** La confianza queda atada a un hash: cada actualización de los scripts de Synapse obliga a revisar de nuevo.
- `--dangerously-bypass-hook-trust` salta la revisión solo para esa invocación.

### 1.9 Versión

No se pudo verificar la primera versión con `PreToolUse`. El motor de hooks llegó en el PR [#13276](https://github.com/openai/codex/pull/13276). La cobertura de `apply_patch` llegó con el PR [#18391](https://github.com/openai/codex/pull/18391) (mergeado 2026-04-22). Fuentes de terceros piden ≥0.125.0 para guards de shell.

> [!NOTE]
> La documentación oficial pide tratar los hooks como *"a useful guardrail, not a complete enforcement boundary"*: algunos caminos especializados pueden quedar fuera y los tools hosteados (WebSearch) no pasan por hooks.

---

## 2. Tools y Forma del Input

| Propósito | `tool_name` | `tool_input` | Impacto en Synapse |
| :--- | :--- | :--- | :--- |
| Shell | `Bash` (shell clásico y unified exec `exec_command`) | `{command: string}`; `workdir` no verificado | Compatible. `write_stdin` no re-dispara `PreToolUse`. |
| Edición / creación / borrado | `apply_patch` | `{command: "<texto del patch>"}` | **Parsear el sobre.** No distingue Write de Edit. |
| Lectura | — | — | **No existe.** Solo shell (`cat`, `sed`, `rg`), MCP o `mcp_resource`. `view_image` para imágenes. |
| MCP | `mcp__<servidor>__<tool>` | argumentos crudos | Un matcher `mcp__.*` cubre lecturas vía MCP (p. ej. `mcp__filesystem__read_file`). |

> [!WARNING]
> Un documento de terceros afirma que unified exec no dispara el hook, lo que contradice la tabla oficial actual. Hay que verificarlo con la versión instalada.

### 2.1 Formato del Sobre `apply_patch`

```text
*** Begin Patch
*** Add File: src/config.py
+API_URL = "https://example.test"
+TOKEN = "..."
*** Update File: src/app.py
*** Move to: src/main.py
@@ def main():
-    old_line()
+    new_line()
*** Delete File: legacy.txt
*** End Patch
```

Reglas para el parser de Synapse:

- **Rutas**: `Add File`, `Update File`, `Delete File` y el destino de `Move to`. Son relativas al `cwd`.
- **Contenido nuevo**: cuerpo completo de `Add File` + líneas `+` de cada hunk de `Update File`.
- **Un patch puede tocar varios archivos**: un `deny` rechaza el patch completo.

Un bridge de terceros dice que el campo es `tool_input.patch`; el código fuente (`codex-rs/core/src/tools/handlers/apply_patch.rs`: `json!({ "command": command })`) y la documentación oficial dicen `command`.

---

## 3. Plugins, Skills y Reglas

### 3.1 Manifiesto

- Recomendado: `plugin.json` portable en la raíz, con lo específico de OpenAI bajo `extensions.com.openai`.
- Alternativas: `.codex-plugin/plugin.json`, y **manifiestos compatibles con Claude** (`.claude-plugin/plugin.json`).
- Estructura: `skills/<nombre>/SKILL.md`, `hooks/hooks.json`, `mcp.json`, `assets/`.

### 3.2 Marketplace

- `.agents/plugins/marketplace.json` (repo), `~/.agents/plugins/marketplace.json` (personal), o `.claude-plugin/marketplace.json` (compatible).
- `codex plugin marketplace add|list|upgrade|remove`; instalación con `/plugins` dentro del CLI.
- Caché: `~/.codex/plugins/cache/<marketplace>/<plugin>/<versión>/`.
- **Los plugins con hooks no son elegibles para el directorio público.**

### 3.3 Skills

`.agents/skills` (desde el `cwd` hasta la raíz del repo), `~/.agents/skills`, `/etc/codex/skills`. **No es `~/.codex/skills`.** Frontmatter `name` + `description` igual que Claude. Se invocan con `$skill` o `/skills`.

### 3.4 Reglas e Instrucciones

Codex lee `AGENTS.md` y `AGENTS.override.md` en `$CODEX_HOME` y desde la raíz del repo hasta el `cwd`, con tope de 32 KiB (`project_doc_max_bytes`). **No hay evidencia de que un plugin pueda incluir reglas o `AGENTS.md`**: `rules/security-guardrails.md` no se cargaría. Opciones: convertirla en skill o documentar un fragmento para `AGENTS.md`.

### 3.5 Variables de Entorno en Hooks

| Variable | Disponible |
| :--- | :--- |
| `PLUGIN_ROOT`, `PLUGIN_DATA` | Sí |
| `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA` | Sí (compatibilidad) |
| `CODEX_HOME` | Sí (equivale a `CLAUDE_CONFIG_DIR`, por defecto `~/.codex`) |
| `CLAUDE_PROJECT_DIR` | **No** (no documentada ni presente en `command_runner.rs`) |
| `CLAUDE_CONFIG_DIR` | **No** |

El hook arranca con un entorno vacío más una copia filtrada del entorno de la sesión.

---

## 4. Mecanismos Complementarios

| Mecanismo | Qué cubre | Limitación |
| :--- | :--- | :--- |
| **execpolicy** (`.rules` en Starlark: `~/.codex/rules/`, `<repo>/.codex/rules/`) | `prefix_rule(pattern=[...], decision="allow"\|"prompt"\|"forbidden", justification=...)`. **Único "ask" real**, para prefijos estáticos (`git push --force`, `git reset --hard`). Se prueba con `codex execpolicy check`. | Experimental. Sin regex ni lógica de rutas. Cadenas con redirecciones, variables o subshells se evalúan como un solo comando. No cubre `apply_patch`. |
| **Sandbox** `workspace-write` | Bloquea a nivel de SO las escrituras fuera del proyecto; `.git`, `.agents`, `.codex`, `.aws` quedan de solo lectura. Red apagada por defecto. | Seatbelt (macOS), bubblewrap + seccomp (Linux/WSL). `/tmp` es escribible. |
| **Perfiles de permisos** (beta) | `[permissions.<n>.filesystem]` con `"**/*.env" = "deny"` bajo `":workspace_roots"`: el respaldo más fuerte para `block_env_read`. Lockfiles y `.venv` en `read`. | En Linux/WSL requiere `glob_scan_max_depth`. No cubre archivos creados después del arranque del proceso. Incompatible con `sandbox_mode`. |
| `approval_policy` | `on-request`, `never` u objeto granular. Con `PermissionRequest` permite auto-aprobar. | `untrusted` se retiró (ahora `projects.<ruta>.trust_level`). |
| `shell_environment_policy` | `ignore_default_excludes=false` elimina del entorno las variables con `KEY`/`SECRET`/`TOKEN`. | Defensa en profundidad, no reemplaza a `block_dangerous`. |
| `notify` | — | Solo `agent-turn-complete`, posterior. **No sirve para guardrails.** |
| Hook `type: "mcp_tool"` | Delegar la decisión a un servidor MCP | Si el servidor falla, **no bloquea**. |

---

## 5. Brechas por Hook

| Hook | Estado | Trabajo |
| :--- | :--- | :--- |
| `block_dangerous` | Matcher `Bash` y `tool_input.command` compatibles | (a) `ask` → `deny` con motivo "requiere confirmación explícita del usuario", o `.rules` con `prompt` para prefijos estáticos. (b) `allow` → `exit 0` vacío. (c) Raíces desde `cwd` + `git rev-parse` (sin `CLAUDE_PROJECT_DIR`); evaluar `workdir`. (d) `git_recoverable_delete` vía `additionalContext`: por verificar. |
| `block_env_read` | Parte shell compatible; `^Read$` nunca matchea | Agregar matcher `mcp__.*` que inspeccione argumentos tipo `path`. Respaldo: perfil de permisos `"**/*.env" = "deny"`. Esquema ofuscado en `permissionDecisionReason`. |
| `detect_secrets` | Matcher compatible por alias; input incompatible | Parsear `apply_patch`: escanear `Add File` completo y líneas `+`. `ask` de Laya → `deny` o advertencia por `additionalContext`. |
| `protect_files` | Matcher compatible por alias; input incompatible | Extraer todas las rutas del patch (incluido `Move to`). Agregar archivos de gobierno de Codex: `.codex/config.toml`, `.codex/hooks.json`, `.codex/rules/*.rules`, `AGENTS.override.md`, `.agents/plugins/marketplace.json`. |

---

## 6. Tabla de Compatibilidad

| Funcionalidad | ¿Soportada? | Notas | Fuente |
| :--- | :--- | :--- | :--- |
| Evento `PreToolUse` | Sí | Activo por defecto; requiere confianza vía `/hooks` | [hooks](https://learn.chatgpt.com/docs/hooks) |
| `hooks.json` con forma de Claude | Sí | Más campos extra | [hooks](https://learn.chatgpt.com/docs/hooks) |
| Hooks dentro de un plugin | Sí | `hooks/hooks.json`; no van al directorio público | [plugins](https://developers.openai.com/plugins/build/plugins) |
| Matcher `Bash`, `^(Edit\|Write)$` | Sí | `apply_patch` por alias | [hooks.md](https://learn.chatgpt.com/docs/hooks.md) |
| Matcher `^Read$` | No | No existe el tool | [handlers](https://github.com/openai/codex/tree/main/codex-rs/core/src/tools/handlers) |
| Bloquear con JSON `deny` | Sí | `permissionDecisionReason` obligatorio | [output_parser.rs](https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/src/engine/output_parser.rs) |
| Bloquear con `exit 2` | Sí (documentado) | Reporte de falla abierta en Windows 0.144 | hooks; [destructive_command_guard](https://raw.githubusercontent.com/Dicklesworthstone/destructive_command_guard/main/docs/codex-integration.md) |
| `ask` | **No** | Falla abierto | output_parser.rs |
| `allow` sin `updatedInput` | **No** | Hook fallido | output_parser.rs |
| `updatedInput` | Sí | | hooks |
| `additionalContext` | Sí | ~2500 tokens | hooks |
| `apply_patch` en `tool_input.command` | Sí (patch crudo) | Parsear sobre | [apply_patch.rs](https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/tools/handlers/apply_patch.rs) |
| `CLAUDE_PLUGIN_ROOT` | Sí | Alias de `PLUGIN_ROOT` | plugins |
| `CLAUDE_PROJECT_DIR` | **No** | Usar `cwd` | [command_runner.rs](https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/src/engine/command_runner.rs) |
| `CLAUDE_CONFIG_DIR` | No | Equivale a `CODEX_HOME` | [config-advanced](https://learn.chatgpt.com/docs/config-file/config-advanced) |
| Manifiesto `.claude-plugin` | Sí (compatibilidad) | | plugins |
| Skills `SKILL.md` | Sí | `.agents/skills` | [build-skills](https://learn.chatgpt.com/docs/build-skills) |
| Regla markdown en plugin | Probablemente no | Usar skill o `AGENTS.md` | [agents-md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) |
| execpolicy `prompt`/`forbidden` | Sí (experimental) | Solo prefijos | [rules](https://learn.chatgpt.com/docs/agent-configuration/rules) |
| Deny de lectura por glob (SO) | Sí (beta) | | [permissions](https://learn.chatgpt.com/docs/permissions) |
| Extensión de IDE | Probablemente igual | Mismo core; hooks no verificados explícitamente | [agent-approvals-security](https://learn.chatgpt.com/docs/agent-approvals-security) |
| Codex cloud | Limitado | Solo hooks MCP globales; los de plugin no corren | hooks; plugins |

---

## 7. Pendiente de Verificar

- Primera versión con `PreToolUse`.
- Si unified exec (`exec_command`) dispara `Bash` y si `tool_input` incluye `workdir`.
- `additionalContext` sin `permissionDecision`.
- Comportamiento de `exit 2` en Linux con la versión actual.
- Si un plugin puede incluir reglas o `AGENTS.md`.
- Si los hooks corren en la extensión de IDE.

**Fuentes secundarias** (solo como señal): [destructive_command_guard](https://raw.githubusercontent.com/Dicklesworthstone/destructive_command_guard/main/docs/codex-integration.md), [agent-hooks-bridge](https://pivanov.github.io/agent-hooks-bridge/adapters/codex.html), [squeez](https://docs.rs/crate/squeez/1.12.0/source/hooks/codex-pretooluse.sh).
