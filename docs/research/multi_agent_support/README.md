# Soporte Multi-Agente: Extensibilidad de Agentes de Código

Investigación sobre **todo lo necesario para construir plugins, extensiones, hooks y mods** para los agentes de código más usados, comparados con los plugins de Claude Code: **OpenAI Codex**, **Google Antigravity** (y Gemini CLI), **OpenCode**, **Cursor**, **GitHub Copilot** y **Herdr**. Cubre manifiestos, todos los eventos de hook, skills, comandos, subagentes, MCP, reglas, configuración y datos, mods de interfaz, LSP, procesos en segundo plano y distribución. Cierra con una guía para construir un plugin multi-agente y un caso de estudio real (Synapse).

**Fecha de corte:** 2026-11-19. Fuentes: documentación oficial, código fuente de cada repo y changelogs. Las fuentes secundarias se marcan como tales y lo no verificado se indica explícitamente en cada documento.

👉 **Empezar por:** [`01-panorama-y-matrices-por-componente.md`](./01-panorama-y-matrices-por-componente.md)

---

## Índice de Archivos

| Archivo | Temática |
| :--- | :--- |
| [`01-panorama-y-matrices-por-componente.md`](./01-panorama-y-matrices-por-componente.md) | Niveles de portabilidad y matrices transversales: manifiesto, hooks (modelo, decisiones, mapeo de eventos, tools), skills, comandos, subagentes, MCP, reglas, config y datos, mods, LSP, distribución |
| [`02-codex.md`](./02-codex.md) | OpenAI Codex: manifiestos (portable y legado), 14 eventos de hook, `apply_patch`, skills y `agents/openai.yaml`, migración de comandos, subagentes TOML, MCP, execpolicy, sandbox, statusline MCP, marketplaces, `requirements.toml` |
| [`03-antigravity-y-gemini-cli.md`](./03-antigravity-y-gemini-cli.md) | Google Antigravity (App, CLI `agy`, IDE) y Gemini CLI: plugins y extensiones, hooks (exit 2 ignorado vs. soportado), skills, workflows, subagentes, MCP, reglas con `trigger`, sidecars, statusline, paneles, `agy plugin import claude` |
| [`04-opencode.md`](./04-opencode.md) | OpenCode V1 y V2: plugins en proceso (server, TUI, GUI, RPC), hooks y transforms, skills, comandos, agentes, tools propios, MCP, permisos, temas, slots TUI, LSP, npm/Git |
| [`05-cursor.md`](./05-cursor.md) | Cursor 3.x: `.cursor-plugin`, 21 eventos de hook (incluidos hooks `prompt`), skills, comandos, subagentes, MCP, reglas `.mdc`, `variables`, import de plugins Claude (falla abierto), marketplace y distribución team |
| [`06-github-copilot.md`](./06-github-copilot.md) | Copilot CLI, app, VS Code (harnesses), cloud agent y JetBrains: Agent Plugins 1.0 y legado, 14 eventos, skills, prompts, agentes, MCP, instrucciones, LSP, extensiones JS del CLI, políticas de org |
| [`07-herdr.md`](./07-herdr.md) | Herdr TUI, herdr-core y app: `herdr-plugin.toml`, 22 eventos, socket API, integraciones con cada agente, OSC 7501, mods JS, y riesgos de ejecución indirecta para plugins de seguridad |
| [`08-guia-plugin-multi-agente.md`](./08-guia-plugin-multi-agente.md) | Estrategia por capas, layout de un repo multi-agente, colisiones de rutas, adaptación de hooks, traducción de cada componente, pruebas, distribución y checklist |
| [`09-caso-de-estudio-synapse.md`](./09-caso-de-estudio-synapse.md) | Synapse: acoplamiento con Claude Code, riesgo de instalarlo sin adaptar en cada agente, arquitectura de adaptadores, plan por fases y verificaciones pendientes |

---

## Cheat Sheet

### Dónde vive cada componente en un plugin

| Componente | Claude Code | Codex | Antigravity | Gemini CLI | OpenCode | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Manifiesto | `.claude-plugin/plugin.json` | `.codex-plugin/` o `.claude-plugin/` | `.antigravity-plugin/plugin.json` | `gemini-extension.json` | `package.json` | `.cursor-plugin/plugin.json` | `plugin.json` o `.claude-plugin/` |
| Hooks | `hooks/hooks.json` | `hooks/hooks.json` | `hooks.json` (raíz) | `hooks/hooks.json` | código TS | `hooks/hooks.json` o ruta propia | `hooks.json` / `hooks/hooks.json` |
| Skills | `skills/` | `skills/` | `skills/` | `skills/` | `ctx.skill.transform` | `skills/` | `skills/` |
| Comandos | `commands/` | → skills | → skills | `commands/*.toml` | `ctx.command.transform` | `commands/` | `commands/` |
| Subagentes | `agents/` | ❌ | `agents/` | `agents/` | `ctx.agent.transform` | `agents/` | `agents/` |
| MCP | `.mcp.json` | `.mcp.json` | `mcp_config.json` | `mcpServers` | `ctx.mcp.transform` | `mcp.json` | `mcp.json` / `.mcp.json` |
| Reglas | `rules/` | ❌ | `rules/` (`trigger`) | `contextFileName` | ❌ | `rules/*.mdc` | `com.github.copilot/rules/` |

### Cómo se bloquea una acción en el hook previo

| Claude | Codex | Antigravity | Gemini | OpenCode | Cursor | Copilot |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `exit 2` | JSON `deny` | `exit 0` + `{"decision":"deny"}` | `exit 2` | `throw` | `exit 2` | `exit 2` |
