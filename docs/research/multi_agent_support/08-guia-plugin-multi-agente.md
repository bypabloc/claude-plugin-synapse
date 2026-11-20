# Guía: Construir un Plugin para Varios Agentes

> Cómo diseñar, empaquetar, probar y distribuir un plugin que funcione en Claude Code, Codex, Antigravity, Gemini CLI, OpenCode, Cursor y Copilot desde un solo repositorio, y cómo convivir con Herdr. Estrategia por capas, colisiones de rutas entre manifiestos, adaptación de hooks, traducción de cada componente y checklist final.

---

## 1. Estrategia por Capas

```text
+------------------------------------------------------------------------------+
| Capa 3 · Específico del host                                                 |
|   temas · statusline · output styles · paneles UI · LSP · monitores/sidecars |
|   -> se construye por host, o se omite                                       |
+------------------------------------------------------------------------------+
| Capa 2 · Traducible (adaptadores)                                            |
|   hooks · subagentes · comandos · configuración/secretos · reglas            |
|   -> un núcleo + archivos o shims por host                                   |
+------------------------------------------------------------------------------+
| Capa 1 · Portable (núcleo)                                                   |
|   skills (name + description) · servidor MCP · instrucciones (AGENTS.md)     |
|   -> se escribe una vez                                                      |
+------------------------------------------------------------------------------+
```

**Regla práctica:** poner la mayor cantidad posible de funcionalidad en la capa 1. Una capacidad expuesta como **tool MCP** funciona igual en todos los agentes (salvo Herdr) y no depende de hooks ni de formatos de comando. Una instrucción expresada como **skill** con `name` + `description` se carga en todos.

| Si el plugin necesita… | Implementarlo como… | Por qué |
| :--- | :--- | :--- |
| Darle una capacidad nueva al agente | Tool en un servidor MCP | Único componente funcionalmente idéntico en todos |
| Enseñarle un procedimiento | Skill | Portable; solo `name`/`description` son fiables |
| Un comando que el usuario invoca | Skill con `disable-model-invocation: true` | Los comandos convergen en skills; Codex y Antigravity convierten `commands/` a skills |
| Interceptar o vetar acciones | Hooks + adaptador por host | No hay alternativa portable; la semántica difiere |
| Contexto permanente | `AGENTS.md` (sugerido al usuario) o hook `SessionStart` | Pocos hosts cargan reglas desde un plugin |
| Pedir configuración o secretos | Variables de entorno documentadas, o OAuth del MCP | `userConfig` solo existe en Claude (y `settings[]` en Gemini) |

---

## 2. Layout de un Repo Multi-Agente

```text
mi-plugin/
├── .claude-plugin/
│   ├── plugin.json                 Claude Code · Codex · Copilot (legado) · VS Code
│   └── marketplace.json            Claude Code · Codex · Copilot
├── .cursor-plugin/plugin.json      Cursor  ("hooks": "./adapters/cursor/hooks.json")
├── .antigravity-plugin/plugin.json Antigravity
├── gemini-extension.json           Gemini CLI (si la raíz se instala como extensión)
├── hooks/hooks.json                Claude Code · Codex · Copilot  ← compartido
├── hooks.json                      Antigravity (raíz, indexado por nombre de hook)
├── adapters/
│   ├── cursor/hooks.json           Eventos Cursor
│   └── opencode/                   Paquete npm (server() V1 + setup() V2)
├── skills/<nombre>/SKILL.md        Todos
├── agents/*.md                     Claude · Antigravity · Gemini · Cursor · Copilot
├── .mcp.json                       Claude · Codex · Copilot
├── mcp.json                        Cursor (y Agent Plugins)
├── mcp_config.json                 Antigravity
├── rules/                          Antigravity (trigger) · Cursor (.mdc) · Claude
├── scripts/                        Lógica de hooks (con permiso 0755)
└── AGENTS.md                       Para el repo del plugin, no se carga desde el plugin
```

### 2.1 Colisiones de Rutas

| Ruta | Quién la lee | Riesgo | Resolución |
| :--- | :--- | :--- | :--- |
| `hooks/hooks.json` | Claude, Codex, Copilot (legado), Gemini (extensión), Cursor (por defecto) | Cursor y Gemini usan otros eventos | Mantener el formato Claude ahí; Cursor apunta a otra ruta vía su manifiesto; Gemini se instala desde otra raíz o se genera en build |
| `hooks.json` (raíz) | Antigravity | — | Sin choque |
| Clave `"hooks"` en la raíz de un JSON estilo Claude | Antigravity | Se cargaría como un hook llamado `hooks` (`validate` advierte) | No mezclar formatos en el mismo archivo |
| `plugin.json` (raíz) | Codex y Copilot (si tiene `$schema` de Agent Plugins), Antigravity | En Codex tiene **prioridad sobre `.claude-plugin/`** y en `main` no carga hooks; en Copilot 1.0 prohíbe rutas de componentes | No poner un `plugin.json` raíz si el plugin tiene hooks; usar los directorios `.<host>-plugin/` |
| `commands/` | Claude, Cursor, Copilot, OpenCode | Antigravity lo rechaza con warning; Codex lo convierte con restricciones | Preferir skills |
| `.mcp.json` vs `mcp.json` vs `mcp_config.json` | Distintos hosts | Duplicación | Generar los tres desde una fuente en el build |
| `AGENTS.md` (raíz del plugin) | Antigravity lo ignora; Codex/OpenCode no lo cargan desde plugins | Falsa expectativa | Reglas en `rules/` con frontmatter por host |

---

## 3. Hooks: el Componente Difícil

### 3.1 Patrón Recomendado: Núcleo + Adaptador

```text
 stdin del host ──▶ normalize(host, payload) ──▶ evento interno ──▶ lógica ──▶ decisión
                                                                              │
 exit/stdout/stderr ◀── emit(host, decisión) ◀────────────────────────────────┘
```

1. **El host se declara** en el comando del manifiesto (`--host cursor`) y la autodetección por forma del payload queda como respaldo.
2. **Normalizar** a un evento interno: tipo de acción (shell, read, write, edit), comando, lista de archivos con contenido nuevo, cwd, raíz del proyecto, sesión.
3. **Emitir** lo que cada host respeta (§3.3).

### 3.2 Detección del Host

| Señal | Host |
| :--- | :--- |
| `turn_id` en el payload, o `PLUGIN_ROOT` sin `CLAUDE_PROJECT_DIR` | Codex |
| `toolCall` + `conversationId` | Antigravity |
| `hook_event_name == "BeforeTool"` o `GEMINI_PROJECT_DIR` | Gemini CLI |
| `CURSOR_VERSION`, `conversation_id`, `workspace_roots` | Cursor |
| `timestamp` ISO sin `permission_mode`, o `COPILOT_HOME` | Copilot |
| Ninguna de las anteriores | Claude Code |

### 3.3 Tabla de Emisión

| Intención | Claude | Codex | Antigravity | Gemini | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Bloquear | `exit 2` | JSON `deny` | **`exit 0`** + `{"decision":"deny"}` | `exit 2` | `exit 2` | `exit 2` |
| Preguntar | `ask` | → `deny` explicado | `ask` / `force_ask` | → `deny` explicado | `ask` (shell/MCP) | `ask` plano |
| Permitir | `allow` | **sin stdout** | `allow` | `exit 0` | `permission: "allow"` | `{}` |
| Contexto | `additionalContext` | `additionalContext` | en `reason` | — | `agent_message` | — |

**Degradación de `ask`:** cuando el host no lo soporta, emitir `deny` con un motivo del tipo *"requiere confirmación explícita del usuario: <motivo>; pídela y reintenta"*. El agente le traslada la pregunta al usuario. Nunca degradar a `allow` silencioso.

### 3.4 Errores Frecuentes al Portar Hooks

- Filtrar por `tool_name == "Bash"`: falla en Cursor-import (`Shell`), Antigravity (`run_command`), OpenCode (`shell`).
- Leer `tool_input.file_path`/`content`: falla con `apply_patch` (Codex, OpenCode, Copilot), PascalCase (Antigravity), `path` (OpenCode V2, Copilot).
- Confiar en `CLAUDE_PROJECT_DIR`: no existe en Codex ni Antigravity; usar `cwd` + `git rev-parse --show-toplevel`.
- Confiar en `CLAUDE_PLUGIN_ROOT` dentro del script: resolver desde `__file__` o `import.meta.url`.
- Terminar con excepción no capturada: en Antigravity y Codex falla abierto; en Copilot (preToolUse) falla cerrado.
- Timeouts largos: Copilot falla abierto a los 30 s.
- Asumir que cada release se aplica sola: Codex exige revisar hooks de nuevo en `/hooks` cuando cambian los scripts; Gemini avisa por cambio de huella.

### 3.5 OpenCode: Shim en TypeScript

OpenCode no ejecuta hooks por subprocess. El plugin TS intercepta con `tool.hook("execute.before")` (V2) o `tool.execute.before` (V1), traduce el evento al formato que entiende el script, lo invoca con `child_process.spawn`, e interpreta el resultado: `exit 2` → `throw`, `ask` → `ctx.permission.assert(...)` (V2), `allow` → `permission.hook("evaluate")`.

---

## 4. Traducción de Cada Componente

### 4.1 Skills

- Usar solo `name` (igual al nombre de la carpeta, kebab-case, ≤ 64) y `description` (≤ 1024).
- `disable-model-invocation` se respeta en Claude, Antigravity, Cursor, OpenCode V2 y VS Code; en Codex va en `agents/openai.yaml` (`policy.allow_implicit_invocation: false`).
- No depender de `allowed-tools` ni `model`.

### 4.2 Subagentes

| Host | Formato | Notas |
| :--- | :--- | :--- |
| Claude, Cursor, Copilot | `agents/*.md` | Cursor ignora `tools`; Copilot usa alias (`execute`, `read`, `edit`) |
| Antigravity | `agents/*.md` | `model` por tier; `tools` con nombres propios |
| Gemini CLI | `agents/*.md` | `max_turns`, `timeout_mins`, `kind: remote` |
| OpenCode | `.opencode/agents/*.md` | Un plugin no puede añadir agentes (no verificado) |
| Codex | `.codex/agents/*.toml` | **No desde plugin**: documentar la copia manual |

### 4.3 MCP

Generar en el build `.mcp.json` (Claude, Codex, Copilot), `mcp.json` (Cursor), `mcp_config.json` (Antigravity) y la clave `mcpServers` de `gemini-extension.json` desde una sola fuente. Referenciar la raíz del plugin con la variable de cada host (`${CLAUDE_PLUGIN_ROOT}`, `${CURSOR_PLUGIN_ROOT}`, `${PLUGIN_ROOT}`, `${extensionPath}`) o con rutas relativas a `cwd` cuando el host lo garantiza.

### 4.4 Configuración y Secretos

| Host | Mecanismo |
| :--- | :--- |
| Claude | `userConfig` |
| Gemini CLI | `settings[]` con `sensitive` (keychain) |
| Antigravity | `variables` en `mcp_config.json` (el plugin queda deshabilitado hasta configurarlo) |
| Cursor | `variables` (los fija un admin) |
| Codex, Copilot, OpenCode | Variables de entorno documentadas u OAuth del MCP |

### 4.5 Reglas

- Antigravity: `rules/<x>.md` con `trigger` válido (o se descarta en silencio).
- Cursor: `rules/<x>.mdc` con `alwaysApply`/`globs`/`description`.
- Copilot: `com.github.copilot/rules/` (formato parcialmente documentado).
- Codex, OpenCode: no se cargan desde plugins; usar un hook `SessionStart` (Codex inyecta stdout como contexto) o pedirle al usuario que agregue un fragmento a su `AGENTS.md`.

### 4.6 Mods de Interfaz

No son portables. Construirlos solo donde aportan:

- Statusline: Claude (plugin), Codex (MCP con `_meta["openai/ui"]`), OpenCode (slots TUI); en Antigravity, Cursor y Copilot es config del usuario.
- Temas: Claude, Gemini (`themes[]`), OpenCode.
- UI propia: OpenCode (TUI/GUI), Copilot (extensiones CLI), Herdr (`[[panes]]`, mods JS).

---

## 5. Convivir con Herdr

- Los plugins de cada agente siguen funcionando dentro de sus panes.
- Para mostrar estado en Herdr: un hook propio (`SessionStart`/`Stop`) que llame a `$HERDR_BIN_PATH pane report-agent` cuando `HERDR_ENV=1`, u OSC 7501 en herdr-core.
- Un plugin de seguridad debe tratar `herdr pane run`, `send-keys`, `agent start -- <flags>` y `hc file write|exec` como ejecución indirecta (ver [`07-herdr.md`](./07-herdr.md#132-riesgos-para-plugins-de-seguridad)).

---

## 6. Pruebas

- **Fixtures por host:** capturar con un hook "sonda" el stdin real de cada herramienta en cada host y versionarlo sanitizado.
- **Matriz de equivalencia:** el mismo caso canónico, traducido a cada host, debe producir la misma decisión interna y una salida válida para el contrato del host.
- **Validadores:** `claude plugin validate --strict`, `agy plugin validate --strict`, `gemini extensions validate`; para Codex, los schemas de `codex-rs/hooks/schema/generated`.
- **E2E reales** contra cada binario, omitidos si no está instalado.

---

## 7. Distribución

- Un solo `.claude-plugin/marketplace.json` sirve a Claude, Codex y Copilot; usar fuentes `url`/`git-subdir` (Codex omite entradas con `source: "github"`).
- Cursor: `.cursor-plugin/marketplace.json` y envío a revisión manual.
- Antigravity: solo marketplace oficial curado (formulario de partners); o instalación por URL de GitHub.
- Gemini CLI: topic `gemini-cli-extension` y manifiesto en la raíz del repo publicado.
- OpenCode: npm o Git.
- Herdr: topic `herdr-plugin`.
- **Codex no admite plugins con hooks en su directorio público.**

---

## 8. Checklist

- [ ] La funcionalidad principal está en MCP o en skills.
- [ ] Skills solo con `name` y `description`; comandos expresados como skills.
- [ ] Sin `plugin.json` en la raíz si el plugin tiene hooks.
- [ ] `hooks/hooks.json` en formato Claude; Cursor apunta a su propio archivo; Antigravity usa `hooks.json` raíz.
- [ ] Scripts de hooks con normalización de entrada y emisión por host.
- [ ] Ningún host falla abierto: bloqueos de Antigravity con `exit 0` + JSON; `ask` degradado a `deny` donde no existe; timeouts explícitos.
- [ ] Raíces de plugin y proyecto resueltas sin depender de variables `CLAUDE_*`.
- [ ] Configuración MCP generada para cada formato.
- [ ] Reglas con frontmatter por host (`trigger`, `.mdc`).
- [ ] Fixtures reales por host y validadores en CI.
- [ ] Documentación por host de lo que no se soporta.
