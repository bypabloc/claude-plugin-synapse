# Panorama y Matriz de Compatibilidad

> Qué hace falta para que Synapse, hoy un plugin de hooks `PreToolUse` para Claude Code, proteja también a Codex, Antigravity (y Gemini CLI), OpenCode, Cursor, GitHub Copilot y a los agentes que corren dentro de Herdr. Estado del ecosistema al **2026-11-19**.

---

## 1. Respuesta Corta

Todos los hosts evaluados, salvo Herdr, permiten interceptar una llamada a un tool **antes** de que se ejecute. Ninguno ejecuta Synapse tal cual sin riesgo:

- **Codex y Copilot** son casi compatibles (mismo `hooks.json`, manifiesto `.claude-plugin`, nombres de tool parecidos), pero **pierden en silencio los `ask`** de Synapse o leen campos que no existen.
- **Cursor** importa los plugins de Claude automáticamente, pero hoy ese import **falla abierto** con Synapse.
- **Antigravity** tiene hooks muy capaces, pero **ignora `exit 2`**, que es la forma en que Synapse bloquea.
- **OpenCode** no tiene hooks por subprocess: exige un **plugin TypeScript** que invoque los scripts Python.
- **Herdr** no es un agente sino un multiplexor: Synapse ya funciona dentro de sus panes, pero hay que cerrar un **bypass por ejecución indirecta** entre panes.

El núcleo de decisión de Synapse (reglas deterministas, Laya, traza) **es reutilizable sin cambios**. El trabajo se concentra en una capa de entrada/salida por host, un parser de `apply_patch` y manifiestos por host. Ver [`09-propuesta-de-arquitectura.md`](./09-propuesta-de-arquitectura.md).

---

## 2. Matriz de Capacidades del Hook Previo

| Capacidad | Claude Code | Codex | Copilot CLI | Cursor | Antigravity | OpenCode | Gemini CLI | Herdr |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Hook antes del tool | ✅ | ✅ | ✅ | ✅ | ✅ (CLI; IDE/App dudoso) | ✅ (plugin TS) | ✅ | ❌ |
| Hook por subprocess + JSON | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | — |
| Bloquear con `exit 2` | ✅ | ✅ (preferir JSON) | ✅ | ✅ | ❌ **falla abierto** | — (`throw`) | ✅ | — |
| Bloquear con JSON | ✅ | ✅ `deny` | ✅ `deny` plano | ✅ `permission` | ✅ `decision` | — | ✅ | — |
| **Preguntar (`ask`)** | ✅ | ❌ falla abierto | ⚠️ (no en autopilot/nube) | ⚠️ solo shell | ✅ `ask`/`force_ask` | ⚠️ V2 sí, V1 no | ❌ → allow | — |
| `allow` explícito | ✅ | ❌ sin `updatedInput` | ✅ | ✅ | ✅ | ⚠️ vía `evaluate` | ✅ | — |
| Contexto al modelo | ✅ | ✅ | ❓ | ✅ `agent_message` | ❌ (solo `reason`) | ❌ | ❌ | — |
| Reescribir input | ✅ | ✅ | ✅ `modifiedArgs` | ⚠️ (bug en `Write`) | ✅ `overwrite` | ✅ | ✅ | — |
| stdin con forma Claude | ✅ | ✅ | ✅ (`PreToolUse`) | ⚠️ | ❌ camelCase | — | ⚠️ casi | — |
| Nombres de tool Claude | ✅ | ⚠️ `Bash` sí; `apply_patch` | ✅ | ⚠️ `Shell` | ❌ | ❌ | ❌ | — |
| Tool de lectura dedicado | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| `CLAUDE_PROJECT_DIR` | ✅ | ❌ | ⚠️ | ✅ alias | ❌ | ❌ | ✅ alias | no lo toca |
| `CLAUDE_PLUGIN_ROOT` | ✅ | ✅ alias | ❓ | ⚠️ solo MCP | ❌ | ❌ | ❌ | — |
| Lee manifiesto `.claude-plugin` | ✅ | ✅ | ✅ (legado) | ✅ (import con bug) | ⚠️ `agy plugin import claude` | ❌ | ❌ | ❌ |
| Skills `SKILL.md` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (lee `.claude/skills`) | ✅ | ❌ |

Leyenda: ✅ soportado · ⚠️ parcial o con condiciones · ❌ no soportado · ❓ no verificado · — no aplica.

---

## 3. Riesgo de Instalar Synapse Sin Adaptador

| Host | ¿Qué pasa hoy si Synapse llega sin cambios? | Severidad |
| :--- | :--- | :--- |
| Codex | Bloqueos funcionan. **Todos los `ask` y `allow` se registran como hook fallido y el tool corre.** `detect_secrets`/`protect_files` leen `content`/`file_path` vacíos en `apply_patch` → pasan. `block_env_read` no ve lecturas (no hay `Read`). | Alta |
| Copilot | Bloqueos por `exit 2` funcionan. `ask`/`allow` anidados probablemente se ignoran. Si los argumentos nativos no son `file_path`/`content`, `detect_secrets`/`protect_files` pasan. | Alta |
| Cursor (import automático) | `tool_name: "Shell"` → `block_dangerous` retorna temprano. **Todo pasa.** | **Crítica** |
| Antigravity | `agy plugin import claude` sin verificar; si importa los hooks, **todo `exit 2` se ignora**. | **Crítica** |
| OpenCode | No carga hooks de Claude. Sin protección (salvo permisos nativos: `*.env` en `ask`). | Sin cobertura |
| Gemini CLI | `gemini hooks migrate` no lee hooks de plugins. Si se migran a mano: bloqueos OK, `ask` → allow. | Media |
| Herdr | Synapse funciona en panes de Claude Code. **Bypass vía `herdr pane run`.** | Media |

---

## 4. Esfuerzo Estimado por Host

| Host | Entrada | Salida | Empaquetado | Esfuerzo relativo |
| :--- | :--- | :--- | :--- | :--- |
| Codex | Parser `apply_patch`; matcher `mcp__.*` | `deny` JSON; degradar `ask`; `allow` vacío | Reutiliza `.claude-plugin` + `hooks/hooks.json` | Bajo–medio |
| Copilot | Mapear argumentos nativos (por confirmar) | Salida plana | Reutiliza `.claude-plugin` + `hooks/hooks.json` | Bajo |
| Cursor | Eventos separados; `Shell` | `permission`/`agent_message` | `.cursor-plugin/` + hooks propios | Medio |
| Antigravity | camelCase/PascalCase; `workspacePaths` | Siempre `exit 0` + `decision` | `.antigravity-plugin/` + `hooks.json` raíz | Medio |
| OpenCode | Shim TS normaliza | `throw` / `permission.assert` | Paquete npm | Alto |
| Gemini CLI | Nombres de tool | Degradar `ask` | Extensión en raíz propia | Medio (opcional) |
| Herdr | — | — | — (reglas nuevas en `block_dangerous`) | Bajo |

---

## 5. Hallazgos de Mercado que Afectan el Plan

- **Gemini CLI se integró en Antigravity** (19 de mayo de 2026) y dejó de servir a usuarios gratuitos y Pro/Ultra (18 de junio de 2026). Antigravity es el destino de Google; Gemini CLI queda opcional.
- **OpenCode 2.x rompió la API de plugins** respecto de 1.x.
- **El formato de hooks de Claude es el estándar de hecho**: Codex, Copilot, Cursor (import), Factory y Devin lo leen total o parcialmente. **No existe una especificación común de hooks**; Agent Plugins 1.0 (agent-plugins.org) estandariza manifiesto, skills y MCP, pero deja los hooks como extensión por cliente.
- **`apply_patch` es el formato de edición dominante fuera de Claude** (Codex, OpenCode con modelos GPT, Copilot, Factory). Un parser propio es inevitable.

---

## 6. Fuera del Alcance de esta Investigación

Se investigaron y se descartaron del documento por decisión de alcance: Windsurf (ahora Devin Desktop, con hooks parecidos a Claude que también leen `.claude/settings.json`), Cline (hooks por script con salida `{cancel, contextModification}`), Kiro (bloquea solo con `exit 2`; JSON `deny` no bloquea), Factory Droid (protocolo casi idéntico a Claude, acepta `.claude-plugin`), Amp (plugin TS con `tool.call` y `ctx.ui.confirm`) y Roo Code (archivado). Si se retoman, el diseño de [`09`](./09-propuesta-de-arquitectura.md) los cubre con nuevas filas en las tablas de nombres y emisión.
