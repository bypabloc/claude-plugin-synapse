# Soporte Multi-Agente para Synapse

Investigación sobre qué hace falta para que Synapse (guardrails `PreToolUse` para Claude Code) funcione en los agentes de código más usados: **OpenAI Codex**, **Google Antigravity** (y Gemini CLI), **OpenCode**, **Cursor**, **GitHub Copilot** y **Herdr**. Incluye una propuesta de arquitectura y un plan de implementación.

**Fecha de corte:** 2026-11-19. Las fuentes son documentación oficial, código fuente de cada repo y changelogs; las fuentes secundarias se marcan como tales y lo no verificado se indica explícitamente.

👉 **Empezar por:** [`01-panorama-y-matriz-de-compatibilidad.md`](./01-panorama-y-matriz-de-compatibilidad.md)

---

## Índice de Archivos

| Archivo | Temática |
| :--- | :--- |
| [`01-panorama-y-matriz-de-compatibilidad.md`](./01-panorama-y-matriz-de-compatibilidad.md) | Respuesta corta, matriz de capacidades por host, riesgo de instalar sin adaptador, esfuerzo estimado |
| [`02-superficie-de-acoplamiento-de-synapse.md`](./02-superficie-de-acoplamiento-de-synapse.md) | Qué ata hoy a Synapse con Claude Code: stdin, salida, variables, manifiestos, rutas |
| [`03-codex-cli.md`](./03-codex-cli.md) | Hooks de Codex, `apply_patch`, ausencia de `Read`, execpolicy, sandbox y perfiles de permisos |
| [`04-antigravity-y-gemini-cli.md`](./04-antigravity-y-gemini-cli.md) | Hooks de Antigravity (exit 2 ignorado, `ask`/`force_ask`), permisos y sandbox; Gemini CLI y `gemini hooks migrate` |
| [`05-opencode.md`](./05-opencode.md) | Plugins TS V1/V2, `execute.before`, `permission.assert`, permisos nativos, shim a Python |
| [`06-cursor.md`](./06-cursor.md) | `beforeShellExecution`, `beforeReadFile`, `preToolUse`, import de plugins Claude (falla abierto), `.cursor-plugin` |
| [`07-github-copilot.md`](./07-github-copilot.md) | Copilot CLI, VS Code (harness Copilot y Local), coding agent en la nube, salida plana, Agent Plugins 1.0 |
| [`08-herdr.md`](./08-herdr.md) | Multiplexor de agentes: Synapse funciona dentro; bypass por ejecución indirecta entre panes |
| [`09-propuesta-de-arquitectura.md`](./09-propuesta-de-arquitectura.md) | Núcleo neutral + adaptadores: `HookEvent`, matriz de emisión, degradación de `ask`, parser de patches, empaquetado |
| [`10-plan-de-implementacion-y-verificaciones.md`](./10-plan-de-implementacion-y-verificaciones.md) | Fases F0–F8, matriz de equivalencia de tests, captura de payloads reales, 18 verificaciones pendientes |

---

## Cheat Sheet

### Dónde vive el hook previo en cada host

| Host | Archivo | Evento | Bloqueo |
| :--- | :--- | :--- | :--- |
| Claude Code | `hooks/hooks.json` (plugin) | `PreToolUse` | `exit 2` |
| Codex | `hooks/hooks.json` (plugin), `.codex/hooks.json` | `PreToolUse` | JSON `permissionDecision:"deny"` |
| Copilot CLI | `hooks/hooks.json` (plugin), `.github/hooks/*.json`, `.claude/settings.json` | `PreToolUse` | `exit 2` |
| Cursor | `.cursor/hooks.json`, `hooks/hooks.json` (plugin) | `beforeShellExecution`, `beforeReadFile`, `preToolUse` | `exit 2` o `permission:"deny"` |
| Antigravity | `hooks.json` (raíz del plugin), `.agents/hooks.json` | `PreToolUse` | **solo** JSON `decision:"deny"` con `exit 0` |
| OpenCode | `.opencode/plugins/` o npm | `execute.before` (V2) / `tool.execute.before` (V1) | `throw` |
| Gemini CLI | `.gemini/settings.json`, `hooks/hooks.json` (extensión) | `BeforeTool` | `exit 2` |
| Herdr | — | — | — (los hooks son del agente que corre en el pane) |

### Cambios mínimos que desbloquean más hosts

1. Resolver la raíz del plugin desde `__file__` cuando falte `CLAUDE_PLUGIN_ROOT`.
2. Aceptar `Shell` como alias de `Bash` y `cwd` vacío (corrige la falla abierta del import de Cursor).
3. Parser de `apply_patch` (Codex, OpenCode, Copilot).
4. Emisión por host con degradación de `ask` → `deny` explicado.
