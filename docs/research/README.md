# Repositorio Central de Investigaciones Técnicas

Este directorio contiene tres investigaciones técnicas independientes, exhaustivas y estructuradas por módulos:

```text
docs/research/
├── laya/                 # Motor de inferencia tipada System 1 (convaiinnovations/laya)
├── claude_plugins/       # Arquitectura, desarrollo y distribución de Plugins para Claude Code
└── multi_agent_support/  # Portabilidad de Synapse a Codex, Antigravity, OpenCode, Cursor, Copilot y Herdr
```

---

## 1. Módulo: Laya — Motor System 1 Tipado

Investigación sobre el modelo y ecosistema **Laya** (`convaiinnovations/laya`), desarrollado por Convai Innovations y Nandha Kishor M. Motor de decisiones tipadas no-autoregresivo de una sola pasada hacia adelante (~33 ms), multilingüe (100+ idiomas), optimizado para enrutamiento, guardrails y clasificación estructurada.

👉 **Índice principal:** [`laya/01-panorama-e-indice.md`](./laya/01-panorama-e-indice.md)

| Archivo | Temática |
| :--- | :--- |
| [`01-panorama-e-indice.md`](./laya/01-panorama-e-indice.md) | Panorama general, comparativa System 1 vs LLMs tradicionales |
| [`02-arquitectura-y-modelos.md`](./laya/02-arquitectura-y-modelos.md) | Backbones ModernBERT y mmBERT, decision heads y token budgets |
| [`03-guia-de-uso-y-primitivas.md`](./laya/03-guia-de-uso-y-primitivas.md) | Primitivas `choice`, `score`, `noul`, esquemas e instalación |
| [`04-el-router-y-multilingue.md`](./laya/04-el-router-y-multilingue.md) | Enrutamiento multilingüe, detección rápida y manejo de 8k tokens |
| [`05-descarga-local-y-modo-offline.md`](./laya/05-descarga-local-y-modo-offline.md) | Descarga local, desconexión de Hugging Face y entornos air-gapped |
| [`06-despliegue-produccion-y-servidores.md`](./laya/06-despliegue-produccion-y-servidores.md) | `laya-serve`, servidores FastAPI, Docker, MCP y SDK TypeScript |
| [`07-optimizacion-y-rendimiento.md`](./laya/07-optimizacion-y-rendimiento.md) | Batching dinámico, INT8 ONNX, TileLang y compilación PyTorch |
| [`08-casos-de-uso-y-patrones-arquitecturales.md`](./laya/08-casos-de-uso-y-patrones-arquitecturales.md) | Guardrails, triaje de tickets, model gateway y flujos FinTech |
| [`09-recetario-de-ejemplos-practicos.md`](./laya/09-recetario-de-ejemplos-practicos.md) | Código listo para copiar: triage omnicanal, LangGraph y batching |
| [`10-tips-comunidad-antipatrones-y-limites-honestos.md`](./laya/10-tips-comunidad-antipatrones-y-limites-honestos.md) | Errores comunes, mitigación de sesgos en `noul` y límites del modelo |
| [`11-guia-de-fine-tuning-y-calibracion.md`](./laya/11-guia-de-fine-tuning-y-calibracion.md) | Entrenamiento RLCD, calibración de temperatura en hardware accesible |

---

## 2. Módulo: Claude Code Plugins

Investigación técnica basada en la especificación oficial de Anthropic (`code.claude.com/docs/en/plugins/*`), patrones de la comunidad y casos de estudio reales.

👉 **Índice principal:** [`claude_plugins/README.md`](./claude_plugins/README.md)

### Proyectos Locales de Referencia

La investigación incorpora y analiza en detalle la arquitectura de dos implementaciones funcionales del usuario:

- **`~/projects/bypabloc/claude-plugins`**: Arquitectura *Marketplace Monorepo Hub*, catálogo centralizado vía `marketplace.json` con fuentes relativas e instalación multi-plugin.
- **`~/projects/bypabloc/claude-plugin-spanish-latam`**: Plugin con motor de inyección dual de hooks (`SessionStart` y `UserPromptSubmit`), reglas canónicas, empaquetado CJS y suite de pruebas unitarias.

### Índice de Archivos

| Archivo | Temática |
| :--- | :--- |
| [`01-panorama-y-arquitectura-plugins.md`](./claude_plugins/01-panorama-y-arquitectura-plugins.md) | Modelo conceptual, scopes (`user`, `project`, `local`), ciclo de vida |
| [`02-anatomia-y-especificacion-de-archivos.md`](./claude_plugins/02-anatomia-y-especificacion-de-archivos.md) | Layout de carpetas, schema `plugin.json`, variables de entorno runtime |
| [`03-guia-paso-a-paso-creacion-y-desarrollo.md`](./claude_plugins/03-guia-paso-a-paso-creacion-y-desarrollo.md) | Flujo paso a paso, testing local con `--plugin-dir`, monorepos |
| [`04-componentes-a-fondo.md`](./claude_plugins/04-componentes-a-fondo.md) | Skills (`SKILL.md`), Subagents, Hooks de ciclo de vida, MCP, LSP |
| [`05-marketplaces-distribucion-y-publicacion.md`](./claude_plugins/05-marketplaces-distribucion-y-publicacion.md) | Schema `marketplace.json`, fuentes (`github`, `relative`, `npm`), CLI |
| [`06-analisis-casos-reales-y-proyectos-bypabloc.md`](./claude_plugins/06-analisis-casos-reales-y-proyectos-bypabloc.md) | Desglose arquitectónico de `claude-plugins` y `claude-plugin-spanish-latam` |
| [`07-tips-comunidad-mejores-practicas-y-antipatrones.md`](./claude_plugins/07-tips-comunidad-mejores-practicas-y-antipatrones.md) | Higiene de contexto, 5 antipatrones críticos, gestión de `${CLAUDE_PLUGIN_DATA}` |
| [`08-recetario-de-ejemplos-practicos.md`](./claude_plugins/08-recetario-de-ejemplos-practicos.md) | 5 implementaciones completas listas para producción |
| [`09-testing-evals-debugging-y-troubleshooting.md`](./claude_plugins/09-testing-evals-debugging-y-troubleshooting.md) | Validación estricta, frameworks de evaluación y matriz de diagnóstico |
| [`README.md`](./claude_plugins/README.md) | Cheat sheet de comandos CLI y navegación rápida |

---

## 3. Módulo: Soporte Multi-Agente

Investigación sobre qué hace falta para que Synapse proteja otros agentes de código además de Claude Code: hooks previos a la ejecución, protocolos de entrada/salida, empaquetado y brechas por host, con una propuesta de arquitectura (núcleo neutral + adaptadores) y un plan por fases. Corte al 2026-11-19.

👉 **Índice principal:** [`multi_agent_support/README.md`](./multi_agent_support/README.md)

| Archivo | Temática |
| :--- | :--- |
| [`01-panorama-y-matriz-de-compatibilidad.md`](./multi_agent_support/01-panorama-y-matriz-de-compatibilidad.md) | Matriz de capacidades, riesgo de instalar sin adaptador, esfuerzo por host |
| [`02-superficie-de-acoplamiento-de-synapse.md`](./multi_agent_support/02-superficie-de-acoplamiento-de-synapse.md) | Qué ata hoy a Synapse con Claude Code |
| [`03-codex-cli.md`](./multi_agent_support/03-codex-cli.md) | OpenAI Codex: hooks, `apply_patch`, execpolicy, sandbox |
| [`04-antigravity-y-gemini-cli.md`](./multi_agent_support/04-antigravity-y-gemini-cli.md) | Google Antigravity y Gemini CLI |
| [`05-opencode.md`](./multi_agent_support/05-opencode.md) | OpenCode: plugins TS V1/V2 y permisos |
| [`06-cursor.md`](./multi_agent_support/06-cursor.md) | Cursor: hooks de agente, import de plugins Claude, `.cursor-plugin` |
| [`07-github-copilot.md`](./multi_agent_support/07-github-copilot.md) | Copilot CLI, VS Code y coding agent |
| [`08-herdr.md`](./multi_agent_support/08-herdr.md) | Herdr: multiplexor de agentes y ejecución indirecta |
| [`09-propuesta-de-arquitectura.md`](./multi_agent_support/09-propuesta-de-arquitectura.md) | Núcleo neutral, matriz de emisión, parser de patches, empaquetado |
| [`10-plan-de-implementacion-y-verificaciones.md`](./multi_agent_support/10-plan-de-implementacion-y-verificaciones.md) | Fases, estrategia de tests, verificaciones pendientes |
