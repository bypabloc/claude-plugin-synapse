# OpenCode

> OpenCode (repo `anomalyco/opencode`, antes `sst/opencode`) evaluado contra los 4 hooks de Synapse. A diferencia de Codex o Gemini, OpenCode **no ejecuta hooks por subprocess con JSON**: los guardrails se escriben como **plugins JS/TS en proceso**. Portar Synapse exige un shim TypeScript que invoque los scripts Python.

---

## 1. Versiones y la Ruptura V1 → V2

| Línea | Versión al 2026-11-19 | Nota |
| :--- | :--- | :--- |
| V1 | `v1.19.6` (17 nov, "Latest" en GitHub) | Sus notas ofrecen subir a 2.0 y advierten *"Plugins made for OpenCode 1 need an update"* |
| V2 | `v2.3.2` (18 nov, tag más reciente) | API de plugins nueva |

**Las APIs de plugins V1 y V2 son incompatibles.** Un plugin V1 no corre en V2 sin reescribirse. Un mismo paquete puede servir a ambas exportando un objeto por defecto con `server()` (V1) y `setup()` (V2). Fuentes: [migrate-v1](https://opencode.ai/docs/build/plugins/migrate-v1/), [server](https://opencode.ai/docs/build/plugins/server/), [releases](https://github.com/anomalyco/opencode/releases).

---

## 2. Interceptar Tools

### 2.1 V2: `execute.before`

```ts
// Esquema conceptual (V2)
setup(ctx) {
  ctx.tool.hook("execute.before", async (ev) => {
    // ev: { tool, input (mutable), sessionID, agent, messageID, id, codemode }
    // throw  -> bloquea (Tool.Error); el modelo ve el mensaje y sigue
    // ctx.permission.assert(...) -> pide confirmación al usuario
  })
}
```

| Capacidad | Mecanismo | Fuente |
| :--- | :--- | :--- |
| **Bloquear** | `throw`. Comentario del core: *"Only tool execute.before may fail: a Tool.Error rejects the call before it runs"*. Equivale a `exit 2`. | `packages/core/src/plugin/hooks.ts`, `packages/plugin/src/promise/adapter.ts` |
| **Preguntar** | (1) `await ctx.permission.assert({sessionID, agent, source:{type:"tool",messageID,id}, action:"synapse.bash", resources:[cmd], metadata:{reason}})`: pide confirmación cuando las reglas resuelven `ask` y lanza error si se niega. (2) `ctx.permission.hook("evaluate", ev => { ev.effect = "ask"; ev.message = ... })` altera resultados allow/ask de permisos nativos. Un `deny` configurado es final y no llega al hook. | [server › Permissions](https://opencode.ai/docs/build/plugins/server/); `packages/plugin/src/promise/permission.ts` |
| **Auto-allow** | `execute.before` no puede saltar el prompt nativo. Usar `permission.hook("evaluate")` con `effect = "allow"` para la misma llamada (requiere correlacionar por sesión + recursos). | server |
| Reescribir input | Mutar `ev.input` / `ev.tool` | adapter.ts |
| Contexto al modelo | Parcial: `execute.after` puede cambiar `result`; `session.hook("context")` agrega texto de sistema. **Nada equivalente en el camino previo.** | server |
| Code mode | El tool `execute` corre JS que llama a otros tools; `execute.before` se dispara también para esas llamadas internas con `codemode: true`. | `core/src/tool.ts` |

### 2.2 V1: `tool.execute.before`

```ts
export const SynapsePlugin = async ({ project, client, $, directory, worktree }) => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool === "read" && output.args.filePath.includes(".env")) {
      throw new Error("Bloqueado por Synapse")
    }
  },
})
```

El hook V1 `permission.ask` está declarado en los tipos, pero el [issue #7006](https://github.com/anomalyco/opencode/issues/7006) reporta que nunca se dispara; los PRs de arreglo [#7077](https://github.com/anomalyco/opencode/pull/7077) y [#12742](https://github.com/anomalyco/opencode/pull/12742) figuran cerrados sin confirmación de merge. **En V1 no hay `ask` confiable desde un plugin.**

> [!NOTE]
> Ni siquiera el proveedor "Claude Agent" de OpenCode, que ejecuta Claude Code, carga los hooks de Claude Code (`docs/content/claude-agent.mdx`).

---

## 3. Permisos Nativos (V2)

Fuente: [permissions](https://opencode.ai/docs/permissions/).

- Decisiones `allow` / `ask` / `deny`. Matching sobre el valor completo con globs `*` y `?`. **Gana la última regla que coincide**; con varios recursos, cualquier `deny` gana.
- Acciones: `read`, `edit` (cubre edit, write y patch), `shell` (antes `bash`), `glob`, `grep`, `webfetch`, `websearch`, `external_directory`, `subagent`, `skill`, `execute`, MCP `<server>_<tool>`.
- Reglas por agente en `agents.<id>.permissions`.
- **Defaults:** `*` allow; lecturas `*.env` y `*.env.*` en **ask**; `*.env.example` allow; `external_directory` **allow** (en V1 era ask).
- El diálogo ofrece `once`, `always` (guarda reglas allow por proyecto) y `reject`.
- `policies` solo puede negar, nunca conceder ni preguntar.

Un `opencode.jsonc` con reglas estáticas sirve como respaldo: `read: {"*.env": "deny"}`, `external_directory: "ask"`, patrones de `shell`.

---

## 4. Tools y Argumentos

| Propósito | V2 | V1 |
| :--- | :--- | :--- |
| Shell | `shell` {`command`, `workdir`, `timeout`, `background`} (desde v2.2.5 `bash` se ejecuta como `shell`) | `bash` {`command`, ...} |
| Lectura | `read` {`path`, `offset`, `limit`} | `read` {`filePath`, ...} |
| Escritura | `write` {`path`, `content`} | `write` {`filePath`, `content`} |
| Edición | `edit` {`path`, `oldString`, `newString`, `replaceAll`} | `edit` {`filePath`, `oldString`, `newString`} |
| Patch | `patch` {`patchText`} (solo para modelos GPT) | `apply_patch` {`patchText`} |
| Búsqueda | `grep`, `glob` | `grep`, `glob` |

`patchText` usa el mismo sobre que Codex (`*** Add File:`, `*** Update File:`, `*** Move to:`, `*** Delete File:`, líneas `+`), con rutas relativas a la raíz del proyecto. `multiedit` no está documentado en V2. Fuentes: `packages/core/src/tool/plugin/*.ts`, [tools](https://opencode.ai/docs/tools/).

---

## 5. Empaquetado y Runtime

| Aspecto | Detalle |
| :--- | :--- |
| Ubicaciones | Autocarga desde `.opencode/plugins/` (proyecto) y `~/.config/opencode/plugins/` (global). |
| Configuración | `plugins: []` (V2; en V1 `plugin`) con nombres npm, `@versión`, rutas locales a **directorios** o `{package, options}`. En V2 un archivo suelto se omite. |
| API | V2: `Plugin.define({id, setup})` desde `@opencode/plugin`. |
| Dependencias | V1: un `package.json` en `.opencode/` recibe `bun install` al arrancar. |
| Runtime | Bun (V1). V2 se construye con Bun y tiene target `build:node`. |
| Invocar Python | V1 entrega `$` (shell de Bun). V2 no tiene `$`: usar `node:child_process` (`spawn`/`execFile`), que funciona en Bun y Node. Cada llamada paga el arranque de Python (estimado 30–80 ms, no medido); el daemon de Laya ya mantiene el modelo caliente. |
| Contexto | V2: `ctx.location.directory`, `ctx.location.project.{id, directory, canonical}`, `sessionID` en el evento, `ctx.options`, `ctx.storage`. V1: `directory`, `worktree`, `project`. **No se definen variables `CLAUDE_*`**: el shim debe pasar `CLAUDE_PROJECT_DIR` y `CLAUDE_PLUGIN_ROOT`. |
| Skills | Lee `.claude/skills/*/SKILL.md`, `~/.claude/skills`, `.agents/skills`. Se apaga con `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` / `OPENCODE_DISABLE_CLAUDE_CODE`. |
| Instrucciones | `AGENTS.md` primero y `CLAUDE.md` como fallback por directorio; `~/.claude/CLAUDE.md` si no hay `~/.config/opencode/AGENTS.md`. **Una regla `.md` incluida en un plugin no se carga.** |

```text
                OpenCode (Bun / Node)                         Synapse
+-------------------------------------------+        +-----------------------------+
| plugin synapse (TS)                       |        |                             |
|  execute.before(ev)                       |        |                             |
|   ├─ normaliza ev.tool/ev.input           | stdin  | hooks/block_dangerous.py    |
|   │   -> {tool_name:"Bash", tool_input}   |------->| hooks/protect_files.py ...  |
|   ├─ spawn python3 (env CLAUDE_*)         |        |                             |
|   └─ interpreta exit/JSON:                |<-------| exit 2 / JSON ask|allow     |
|        exit 2  -> throw Error(reason)     |        |                             |
|        ask     -> permission.assert(...)  |        |                             |
|        allow   -> evaluate.effect="allow" |        |                             |
+-------------------------------------------+        +-----------------------------+
```

---

## 6. Brechas por Hook

| Hook | Trabajo |
| :--- | :--- |
| `block_dangerous` | Mapear `shell`/`bash` + `command` (y `workdir` como cwd) a `{tool_name:"Bash", tool_input:{command}}`. `exit 2` → `throw`. `ask` → `permission.assert` (V2); sin equivalente confiable en V1. Auto-allow vía `evaluate`. |
| `block_env_read` | `read.path` / `read.filePath` → `Read.file_path`. Cubrir también `grep`/`glob`. La regla nativa `*.env` ya pide confirmación; un `deny` configurado gana sobre cualquier plugin. |
| `detect_secrets` | `write.content`, `edit.newString`. Para `patch`/`apply_patch`, parsear líneas `+` de `patchText`. |
| `protect_files` | `path` o marcadores del patch (varios archivos por llamada). Agregar `opencode.json(c)` y `.opencode/plugins/**`. Respaldo con `external_directory`. |

---

## 7. Tabla de Compatibilidad

| Funcionalidad | ¿Soportada? | Notas |
| :--- | :--- | :--- |
| Hook previo por subprocess (formato Claude) | **No** | Solo plugins JS/TS en proceso |
| Hook previo (plugin) | Sí | V2 `execute.before`, V1 `tool.execute.before` |
| Bloquear | Sí | `throw` |
| Preguntar | V2 sí, V1 no confiable | `permission.assert` / `evaluate` |
| Auto-allow | V2 vía `evaluate` | Requiere correlación |
| Reescribir input | Sí | |
| Contexto previo al modelo | No | Solo después (`execute.after`) o a nivel sesión |
| Skills Claude | Sí | Lee `.claude/skills` |
| `AGENTS.md`/`CLAUDE.md` | Sí | Regla de plugin no |
| Variables `CLAUDE_*` | No | El shim las inyecta |

---

## 8. Pendiente de Verificar

- Si el V1 `permission.ask` se arregló en alguna versión.
- Latencia real de `spawn` de Python por llamada desde Bun y Node.
- Comportamiento de `evaluate` cuando varias llamadas concurrentes comparten recursos.
