# Caso de Estudio: Portar session-handoff a Otros Agentes

> Segundo caso real: `session-handoff` (`0.4.1`), un **mod en TypeScript** de Claude Code que guarda el estado del trabajo en disco, lo reinyecta, impide cerrar el turno con trabajo pendiente, compacta sin resumen del modelo y dibuja métricas sobre el prompt. A diferencia de Synapse (hooks por subprocess), usa la **API de mods** de Claude Code. Este documento describe esa API (línea base que faltaba en la investigación), evalúa cada función del plugin en cada agente y propone un diseño de port. Verificado en una tercera ronda de investigación. Corte al 2026-11-19.

---

## 1. Qué Hace el Plugin y con Qué API

Repositorio: `bypabloc/claude-plugin-session-handoff`. Declaración en `hooks/hooks.json`: `{"modules": ["./register.tsx"]}`. Manifiesto con `"types": "./types/index.d.ts"` para tipar el estado (`PluginState`).

| Función | Evento o API de mods que usa |
| :--- | :--- |
| Reinyectar el handoff al iniciar, reanudar, `/clear` y compactar | `classic.SessionStart` → `additionalContext` |
| No dejar cerrar el turno con `Next step` pendiente (tope de 3, `external-wait`) | `classic.Stop` → `block` |
| Rechazar `AskUserQuestion` con una spec en curso | `tool.call` con filtro `{tool: 'AskUserQuestion'}` → `{deny}` |
| Avisar al escribir `PROGRESS.md` mal formado; pedir el hash tras `git commit`; contar tests y ediciones | `classic.PostToolUse` → `block` (feedback); lee `tool_response` |
| Recordar hashes pendientes en cada mensaje | `classic.UserPromptSubmit` → `additionalContext` |
| **Compactar sin resumen del modelo** (tramo reciente de ~20k tokens + handoff) | `session.compact` → `{messages}` o `{skip}` |
| Métricas: costo, tokens, duración, caché, rate limits, `ctx %` | `session.start`, `turn.start`, `turn.complete` (`usage`, `durationMs`), `session.measure` (`context.percent`, `cost`, `rateLimits`), `$.session.usage()` |
| Spinner con fase y ETA | `ui.render` `{component: 'Spinner'}` |
| Banda sobre el prompt con botones `Less`/`More`/`Hide` | `ui.render` `{component: 'AbovePrompt'}` + `$.ui.resolve(e)` (`Box`, `Text`, `Button`) |
| Estado por sesión que sobrevive a `--resume` | `atom`/`read`/`update` (`$.state`) + `$.store` |
| Toasts, lectura de archivos, procesos | `$.ui.toast`, `$.fs.read`, `$.process.run` |

### 1.1 Qué Parte es Portable

| Módulo | Dependencia de la API de mods | Portabilidad |
| :--- | :--- | :--- |
| `handoff.ts` (formato, fases, textos, decisiones de `Stop`) | Ninguna | **Portable tal cual** |
| `store.ts` (ubicaciones, vínculos, bitácora) | Interfaz `Io` propia (inyectable) | **Portable** con otra implementación de `Io` |
| `status.ts`, `metrics.ts` (textos de banda, cálculo de métricas, conteo de tests) | Ninguna | **Portable** |
| `tail.ts` (recorte del tramo reciente) | Tipo `SessionMessage` | Portable solo donde el host entregue mensajes reemplazables |
| `register.tsx` (cableado de eventos y UI) | Total | Se reescribe por host |

El plugin ya está separado en núcleo y cableado: **portar significa escribir un `register` por host**, no reescribir la lógica.

---

## 2. Línea Base: la API de Mods de Claude Code

Fuentes: [mods/reference](https://code.claude.com/docs/en/plugins/mods/reference.md), [mods/events](https://code.claude.com/docs/en/plugins/mods/events.md), [mods/interface](https://code.claude.com/docs/en/plugins/mods/interface.md), [mods/api](https://code.claude.com/docs/en/plugins/mods/api.md); código del plugin; binario local de Claude Code 2.1.327.

### 2.1 Mod vs. Hook por Subprocess

| | Hook classic | Mod |
| :--- | :--- | :--- |
| Declaración | Comando, HTTP, prompt o agente en `settings.json` o `hooks/hooks.json` | `hooks/hooks.json` → `{"modules": ["./register.tsx"]}` |
| Ejecución | Subproceso, JSON por stdin | **En proceso**, como middleware |
| Capacidad | Observar o bloquear en puntos fijos | Reescribir o responder el evento en lugar de Claude Code; dibujar UI; estado propio |
| Runtime | El que use el script | El motor de Claude Code; **sin APIs de Node** (solo `URL`, `TextEncoder`, `AbortController`, `crypto.subtle`) |
| Instalación extra | Intérprete del script | **Ninguna** |

Los hooks classic también llegan al mod como eventos `classic.<Evento>` (con el stdin del hook como `e`). Versión mínima del plugin: 2.1.287; la referencia cubre la CLI desde 2.1.290.

### 2.2 Registro y Cadena

```ts
import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

export const register: Register = on => {
  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    if (debeRechazar) return { deny: 'motivo' }   // responde y corta la cadena
    return next(e)                                 // sigue: siguiente handler o Claude Code
  })
}
```

- Filtros: `{tool}`, `{component}`, `{command}`, `{plugin}` (string, array o RegExp); comodines `'classic.*'` y `'*'`.
- `next(e)` continúa; `next({...e, campo})` pasa una copia modificada; devolver un objeto sin llamar a `next` responde el evento. `e` está congelado.
- Un solo `on` sin filtro por evento (registrar dos veces `session.start` sin filtro hace fallar el módulo).
- Fallos: si un hook lanza o excede su tiempo antes de `next`, se omite; `.catch(handler)` permite fail-closed (`next.called`, `next.error.kind`).
- Orden: tiers `sec-default` → mods del usuario → `appendPlugins` → builtin.

### 2.3 Eventos Principales

| Evento | Momento | Payload | Puede devolver |
| :--- | :--- | :--- | :--- |
| `tool.call` | Antes de ejecutar un tool | `tool`, argumentos, `tool_use_id` | `next(e)`, `{deny}`, `{result}` |
| `tool.check` | Tras `PreToolUse`, antes del prompt de permisos | `input`, `agentId`, `ceiling`, `isReadOnly` | `{decision: allow\|ask\|deny, reason}` |
| `prompt.submit` | Al enviar un prompt | `text`, `context` | `next({...e, text\|context})`, `{drop}` |
| `turn.start` / `turn.step` / `turn.complete` | Inicio, cada request, fin del turno | `turnId`, `agentId`; `turn.complete`: `answer`, `durationMs`, `usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`), `isAborted` | `turn.step`: cambiar `model`/`effort`; `turn.complete`: `{text}` |
| `session.start` / `session.end` | Carga del mod / fin de sesión | `session.end`: `reason` | `next(e)` |
| `session.compact` | Antes de compactar | `trigger` (incluye `precompute`), `messages`, `agentId`, `toolUses` | `{skip}` (documentado); `{messages}` (usado por el plugin, no documentado en las páginas leídas) |
| `session.measure` | Tras cada turno y al cambiar un límite | `context.percent`, `changed`, `cost{usd, hasUnknownPrice}`, `rateLimits[{kind, percentUsed, resetsAt}]` | `next(e)` |
| `session.receive` / `session.send` / `session.append` | Mensajes entre sesiones / transcript | `text`, `origin`, `to`, `message` | `{consumed}`, `{isDelivered: false}`, `next({...e, message})` |
| `ui.render` | Antes de dibujar un sitio | `component`, `props`, `requestId`, `surface`, `viewport` | Árbol de `$.ui.resolve(e)` o `next(e)` |
| `classic.<Evento>` | Hooks de settings | stdin del hook | Campos de salida del hook (`block`, `additionalContext`) |

Sitios de `ui.render`: `Spinner` (`word`, `message`, `suffix`, `mode`), `AbovePrompt` (`hasSurvey`, `isCollapsed`, `isWorking`, `maxRows`, `bodyColumns`), `Pane`, `AbovePromptList`, `UserMessage`, `AssistantMessage`, `ToolUse`, `ToolResult`, `AskUserQuestion`, `PromptHint`, entre otros.

### 2.4 El Objeto `$` y el Estado

| API | Uso |
| :--- | :--- |
| `$.ui` | `resolve(e)` (`Box`, `Text`, `Button`, `Link`, `Code`, `Markdown`, `Input`, `Select`…), `toast`, `open`/`close` (panes), `notify`, `invalidate` |
| `$.session` | `usage()` → `{startedAt, context{tokens, window, percent}, rateLimits, cost}`; `messages()`, `cwd()`, `id()`, `compact`, `send`, `append` |
| `$.state` (atoms) | `atom({plugin, key}, default)` + `read`/`update`; vive durante la sesión y sobrevive a recargar el mod; tipado con `PluginState` |
| `$.store` | `get`/`set`/`delete`/`keys`; persiste entre sesiones; **compartido por todas las sesiones de la máquina**; 4 MiB; no atómico |
| `$.fs` | `read`, `write`, `list`, `exists`, `stat`, `ancestors`; 4 MiB por archivo |
| `$.process.run(argv)` | Sin shell; timeout 30 s (máx. 10 min) |
| `$.clock`, `$.http.fetch`, `$.model.*`, `$.mcp.*`, `$.command.*`, `$.tool.*`, `$.theme` | Otros servicios |

**Límites:** 10 s por hook y evento (50 ms en `prompt.edit`); redibujado máx. 10/s (30 en terminal); árboles ≤ 32 niveles y 20 000 elementos; `ui.render` no puede escribir estado.

**Pruebas y validación:** `claude plugin test` (API `claude-code/testing`) y `claude plugin validate [--strict]`.

---

## 3. Viabilidad de Cada Función por Agente

Leyenda: ✅ equivalente · ⚠️ degradado o con condiciones · ❌ no posible · ❓ no verificado.

| Función | Codex | Antigravity | Gemini CLI | OpenCode V2 | OpenCode V1 | Cursor | Copilot CLI |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Reinyectar al iniciar | ✅ `SessionStart` | ✅ `SessionStart` (`injectSteps`) | ✅ `SessionStart` | ✅ `session.hook("context")` | ✅ `system.transform` | ✅ `sessionStart` (`additional_context`) | ✅ `sessionStart` |
| Reinyectar tras compactar | ✅ `SessionStart` `source: "compact"` | ⚠️ `PreInvocation` en cada invocación (sin evento de compactación) | ❌ `SessionStart` no tiene `compact` | ✅ `session.hook("context")` (en cada llamada) | ⚠️ `context[]` antes del resumen | ❌ | ❌ |
| Bloquear el fin del turno | ✅ `Stop` `decision: "block"` (+ `stop_hook_active`) | ✅ `Stop` `decision: "continue"`; `PostInvocation` `force_continue` | ✅ `AfterAgent` `decision: "deny"` | ⚠️ no bloquea: `ctx.session.prompt` tras idle (disparador no confirmado) | ⚠️ `session.idle` + `client.session.promptAsync` | ✅ `stop` `followup_message` | ✅ `agentStop` `decision: "block"` |
| Tope de bucle nativo | ❌ (propio) | Existe, sin número documentado | ❌ (propio) | ❌ (propio) | ❌ (propio) | ✅ `loop_limit` (5) | ✅ 8 consecutivos |
| Rechazar la pregunta al usuario | ❓ `request_user_input` (hooks no lo listan) | ✅ `ask_question` en `PreToolUse` | ❓ `ask_user` | ⚠️ `question` vía `execute.before` (no confirmado) | ⚠️ ídem | ❌ (preToolUse no lo expone) | ✅ `ask_user` → `AskUserQuestion` |
| Feedback tras escribir un archivo | ✅ `PostToolUse` (con `apply_patch`) | ❌ `PostToolUse` sin salida ni contexto | ✅ `AfterTool` | ✅ `execute.after` | ✅ | ✅ `postToolUse` | ✅ `postToolUse` |
| Leer la salida de un comando (commit, tests) | ✅ `tool_response` | ❌ | ✅ `tool_response` | ✅ | ✅ | ✅ `afterShellExecution.output` | ✅ `toolResult` |
| Contexto en cada mensaje | ✅ `UserPromptSubmit` | ✅ `PreInvocation` | ✅ `BeforeAgent` | ✅ `session.hook("prompt")` | ✅ `chat.message` | ❌ solo `continue` | ❓ |
| **Compactar sin resumen** | ❌ (solo `compact_prompt` y umbral) | ❌ | ❌ (solo umbral) | ✅ `session.hook("compaction")` con `result` | ⚠️ `output.prompt` reemplaza el prompt de resumen | ❌ | ❌ |
| Métricas (costo, tokens, `ctx %`, límites) | ⚠️ `PostModelRequest.token_usage` | ✅ en el stdin de la statusline (usuario) | ⚠️ `totalTokenCount` | ⚠️ tokens/costo en mensajes (nombres no verificados) | ⚠️ ídem | ❓ statusline sin schema | ❓ statusline sin schema oficial |
| Banda / statusline **desde el plugin** | ⚠️ statusline vía MCP (experimental; verificado en código, sin doc oficial) | ❌ (solo usuario) | ❌ | ✅ slots TUI | ✅ slots TUI | ❌ (solo usuario) | ❌ (solo usuario) |
| Botones interactivos | ❌ (árbol con `link`) | ❌ | ❌ | ✅ (Solid/OpenTUI) | ✅ | ❌ | ❌ |
| Spinner | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Estado persistente | `PLUGIN_DATA` | `PLUGIN_DATA` | archivos propios | `ctx.storage` | archivos / `api.kv` | archivos propios | `PLUGIN_DATA` |
| Runtime sin instalar nada | ❌ (binario compilado) | ❌ (binario compilado) | ❌ (Node ≥ 20 ya presente) | ✅ **Bun embebido** | ✅ Bun embebido | ❌ (binario compilado) | ❌ (binario; extensiones JS piden Node) |

### 3.1 Detalle de Compactación

| Agente | Reemplazar | Prompt propio | Umbral / desactivar | Fuente |
| :--- | :--- | :--- | :--- | :--- |
| Codex | ❌ (`PreCompact`/`PostCompact` solo `continue:false`) | ✅ `compact_prompt`, `experimental_compact_prompt_file` | Umbral `model_auto_compact_token_limit` (+ `_scope`); sin clave para desactivar | [hooks](https://learn.chatgpt.com/docs/hooks.md), [config-reference](https://learn.chatgpt.com/docs/config-file/config-reference.md) |
| Antigravity | ❌ | ❌ | No documentado | [hooks](https://antigravity.google/docs/hooks) |
| Gemini CLI | ❌ (`PreCompress` consultivo) | ❌ | `model.compressionThreshold` (0.5), `contextManagement.historyWindow.*` | [hooks/reference](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/hooks/reference.md), [configuration](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/reference/configuration.md) |
| OpenCode V2 | ✅ `messages` mutables + `result` salta el modelo | ❌ | No verificado; `ctx.session.compact` pide compactar | [plugins/server](https://opencode.ai/docs/build/plugins/server/) |
| OpenCode V1 | ❌ | ✅ `experimental.session.compacting` → `output.prompt` (+ `context[]`) | `compaction.auto: false` desactiva | [v1/plugins](https://opencode.ai/v1/docs/plugins/), [v1/config](https://opencode.ai/v1/docs/config/) |
| Cursor | ❌ (`preCompact` observa: `context_usage_percent`, `message_count`…) | ❌ | No documentado | [hooks](https://cursor.com/docs/agent/hooks) |
| Copilot CLI | ❌ (`preCompact` notifica) | ❌ | Fijo (~80 % en segundo plano, pausa ~95 %) | [context-management](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/context-management) |

### 3.2 Detalle de Telemetría

| Agente | Costo | Tokens | `ctx %` | Rate limits / cuota | Dónde |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Antigravity CLI | ✅ `cost{total_usd, subagent_usd, estimated}` | ✅ `context_window{total_input_tokens, total_output_tokens, current_usage}` | ✅ `used_percentage` | ✅ `quota{<cubo>: {remaining_fraction, reset_in_seconds}}` | stdin de `statusLine` ([cli/statusline](https://antigravity.google/docs/cli/statusline)) |
| Codex | ❌ | ⚠️ `PostModelRequest.token_usage` (campos no documentados) | ❌ | ❌ | Hooks |
| Gemini CLI | ❌ | ⚠️ `AfterModel` `usageMetadata.totalTokenCount` | ❌ | ❌ | Hooks |
| OpenCode | ⚠️ calculable | ⚠️ en mensajes / `ctx.session.generate` | ⚠️ `limit.context` + uso | ❌ (solo `retry` con 429) | Eventos y plugin |
| Cursor CLI | ❓ | ❓ | ❓ | ❌ | `statusLine` (schema no publicado) |
| Copilot CLI | ❓ (guías: premium requests, no USD) | ❓ | ❓ | ⚠️ `footer.showQuota` | `statusLine` (campos solo en guías de terceros) |

Ningún agente entrega duración por turno como campo.

### 3.3 Detalle de Runtime

| Agente | Runtime necesario para la lógica | Runtime embebido usable | Binario compilado | Fuente |
| :--- | :--- | :--- | :--- | :--- |
| Codex | Hooks = comandos (`$SHELL -lc`) | ❌ (CLI = binario nativo; Node solo para el launcher npm) | ✅ por plataforma | [registry](https://registry.npmjs.org/@openai/codex/latest) |
| Antigravity | Hooks = comandos | ❌ | ✅ (cuidado: exit ≠ 0 falla abierto) | [build-plugins](https://antigravity.google/docs/build-plugins) |
| Gemini CLI | Hooks = comandos | ❌ (pero el CLI exige Node ≥ 20, así que hay Node) | ✅ | [registry](https://registry.npmjs.org/@google/gemini-cli/latest) |
| OpenCode | Plugin en proceso | ✅ **Bun embebido** (`Bun.build` con `compile`) | ✅ como proceso hijo | [build.ts](https://raw.githubusercontent.com/anomalyco/opencode/v1.19.6/packages/opencode/script/build.ts), [registry](https://registry.npmjs.org/opencode-ai/latest) |
| Cursor | Hooks = comandos | ❓ | ✅ | [cli/installation](https://cursor.com/docs/cli/installation) |
| Copilot | Hooks = comandos; extensiones JS "run with Node.js" | ❓ | ✅ | [create-an-extension](https://docs.github.com/en/copilot/tutorials/create-an-extension) |

---

## 4. Diseño de Port Recomendado

### 4.1 Arquitectura

```text
                       ┌──────────────────────────────────────────┐
                       │ núcleo TS (sin cambios)                  │
                       │ handoff.ts · store.ts(Io) · status.ts ·  │
                       │ metrics.ts                               │
                       └───────────────┬──────────────────────────┘
          ┌────────────────────────────┼─────────────────────────────┐
          ▼                            ▼                             ▼
 register.tsx (Claude, mod)   opencode/plugin.ts (en proceso)   cli.ts (binario por plataforma)
 Io = $.fs / $.process        Io = fs de Bun / ctx.storage      Io = fs de Bun/Node
 UI = ui.render               UI = slots TUI                    stdin/stdout de hooks
                                                                 ├─ Codex · Copilot (hooks/hooks.json)
                                                                 ├─ Cursor (.cursor-plugin, ruta propia)
                                                                 ├─ Antigravity (hooks.json raíz, exit 0)
                                                                 └─ Gemini (extensión)
```

- **Un binario** `session-handoff-hook` compilado con `bun build --compile` por plataforma (linux-x64/arm64, darwin-arm64/x64, windows-x64) que implementa `Io` sobre el sistema de archivos, lee el payload del host, normaliza el evento y emite la salida del host. Reutiliza el núcleo sin cambios. Conserva la propiedad "no hace falta instalar nada" a cambio de distribuir binarios.
- **OpenCode** usa el mismo núcleo en proceso (Bun embebido): no necesita binario.
- **Estado de sesión** (meter, `lastStop`, commits pendientes): en el host con mods vive en `$.state`/`$.store`; en los demás pasa a un archivo por sesión en `PLUGIN_DATA` o en `tmp/handoff/.state/<session>.json`, porque cada invocación del hook es un proceso nuevo.

### 4.2 Por Agente

| Agente | Qué se porta | Qué se degrada | Cómo |
| :--- | :--- | :--- | :--- |
| **Codex** | Reinyección (incluida tras compactar), `Stop`, feedback de `PROGRESS.md` (parseando `apply_patch`), hashes de commits, recordatorios | Compactación con resumen del modelo (se usa `compact_prompt` para pedir un resumen mínimo y el handoff entra por `SessionStart source=compact`); métricas reducidas a tokens; banda como statusline MCP experimental o nada | Binario + `.claude-plugin`/`hooks/hooks.json` compartido (no usar `plugin.json` raíz). Contador propio de bloqueos (`stop_hook_active`) |
| **Copilot CLI** | Reinyección al iniciar, `agentStop` (tope 8), rechazo de `ask_user`, feedback, hashes | Sin reinyección tras compactar (compactación fija al ~80 %); sin banda desde plugin | Binario + `hooks/hooks.json` con eventos PascalCase. El handoff entra también por `postToolUse` o en cada `agentStop` como respaldo |
| **Cursor** | Reinyección al iniciar, `stop` con `followup_message` (`loop_limit`), feedback, salida de shell | Sin contexto por mensaje, sin rechazo de la pregunta, sin reinyección tras compactar, sin banda | Binario + `.cursor-plugin` con `"hooks": "./adapters/cursor/hooks.json"`. Respaldo: regla `.mdc` siempre activa que indique leer `tmp/handoff/<trabajo>/PROGRESS.md` |
| **Antigravity** | Reinyección (`SessionStart` + `PreInvocation` en cada invocación, que cubre la compactación), `Stop` `continue`, rechazo de `ask_question` | Sin feedback tras escribir ni lectura de salidas (no se detectan commits ni tests desde hooks); métricas solo en la statusline del usuario | Binario con **salida siempre `exit 0` + JSON**; `hooks.json` raíz. Documentar un script de statusline opcional para el usuario, que ya recibe costo, tokens, `ctx %` y cuota |
| **Gemini CLI** | Reinyección al iniciar, `AfterAgent` deny, feedback y salida de tools, contexto por mensaje | Sin reinyección tras compactar; sin banda | Binario (o script Node, porque el CLI ya trae Node ≥ 20) + extensión; subir `model.compressionThreshold` reduce compactaciones |
| **OpenCode V2** | **Todo lo esencial, incluida la compactación sin resumen** (`session.hook("compaction")` con `result`) y la banda (slots TUI con botones) | Bloqueo del turno se reemplaza por `ctx.session.prompt` tras idle (confirmar el evento) | Plugin TS en proceso con el núcleo; `tail.ts` adaptado al tipo de mensaje de OpenCode |
| **OpenCode V1** | Reinyección, recordatorios, prompt de compactación propio | Sin reemplazo real de la compactación; continuación vía `session.idle` + `client.session.promptAsync` | Mismo paquete con `server()` V1 |
| **Herdr** | No aplica como host | — | Opcional: el binario reporta estado con `herdr pane report-agent` cuando `HERDR_ENV=1`, y Herdr lo muestra en el sidebar |

### 4.3 Lo que No se Puede Portar

- **Compactación sin resumen**: solo OpenCode V2. En el resto hay que aceptar el resumen nativo y reinyectar el handoff después (Codex) o en cada invocación (Antigravity), o vivir sin reinyección tras compactar (Gemini, Cursor, Copilot), compensando con recordatorios en `Stop` y en cada mensaje.
- **Spinner**: ningún agente lo expone.
- **Banda interactiva con botones**: solo OpenCode.
- **Métricas completas**: solo Antigravity las entrega, y únicamente a la statusline que configura el usuario.

---

## 5. Respuesta a la Pregunta Original

¿Sirve la investigación general (documentos 01–08) para un plugin como este? **Sí para la mitad de hooks y empaquetado, no para la mitad de mods.** Antes de esta ronda faltaban:

1. La línea base de la API de mods de Claude Code (ahora en §2).
2. La capacidad de reemplazar la compactación (§3.1).
3. Los nombres de la herramienta de preguntas en cada agente (§3).
4. La telemetría disponible por host (§3.2).
5. La continuación forzada en OpenCode (§3).
6. El runtime disponible para ejecutar lógica sin instalar nada (§3.3).

**Lección para la guía general** ([`08-guia-plugin-multi-agente.md`](./08-guia-plugin-multi-agente.md)): los plugins que dependen de **comportamiento en proceso** (reescribir la compactación, dibujar UI, leer telemetría interna) son mucho menos portables que los que solo observan y vetan acciones. La estrategia que funciona es la misma que ya aplica este plugin: **núcleo puro + cableado por host**, con OpenCode como el único host cuya API de plugins se acerca a la de mods.

---

## 6. No Verificado

- `{messages}` como retorno de `session.compact` y los campos de `session.measure` en la documentación oficial (solo verificados en el código del plugin).
- Si `request_user_input` (Codex) y `ask_user` (Gemini) pasan por el hook previo a tools.
- Argumentos de las herramientas de preguntas de Antigravity, OpenCode y Cursor (nombre exacto en Cursor).
- Si `question` (OpenCode) pasa por `execute.before`.
- Evento de "sesión inactiva" en OpenCode V2 para disparar `ctx.session.prompt`.
- Claves de compactación de OpenCode V2 (umbral o desactivar) y de Antigravity.
- Si `PreCompact` con `continue:false` en Codex detiene solo la compactación o el turno completo.
- Statusline vía MCP de Codex: verificada en el código (skill `create-statusline`, release 0.171.0) pero sin documentación oficial.
- Payloads de `statusLine` en Cursor y Copilot (sin schema oficial).
- Si las extensiones JS de Copilot usan un Node embebido.
- Campos de `token_usage` en Codex y de tokens/costo en mensajes de OpenCode.
