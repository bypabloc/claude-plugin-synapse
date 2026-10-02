# Claude Code Plugins: Investigación Técnica y Guía Exhaustiva

> Investigación integral, arquitectura interna, guía paso a paso, recetario de ejemplos, análisis de proyectos de referencia (`bypabloc`) y mejores prácticas para el desarrollo de plugins en Claude Code.

---

## 📌 Proyectos Locales de Referencia

Esta investigación toma como base de análisis empírico y caso de estudio dos proyectos funcionales en producción:

1. **[`~/projects/bypabloc/claude-plugins`](file:///home/bypabloc/projects/bypabloc/claude-plugins)**
   - **Patrón:** *Marketplace Monorepo Hub*.
   - **Propósito:** Repositorio centralizador de catálogo de plugins para distribución local y remota mediante `marketplace.json` con resolución relativa de fuentes.
   - **Desglose arquitectónico:** [`06-analisis-casos-reales-y-proyectos-bypabloc.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/06-analisis-casos-reales-y-proyectos-bypabloc.md).

2. **[`~/projects/bypabloc/claude-plugin-spanish-latam`](file:///home/bypabloc/projects/bypabloc/claude-plugin-spanish-latam)**
   - **Patrón:** *Dual-Hook Dialect Enforcement Engine*.
   - **Propósito:** Inyección determinista de directivas de español neutro latinoamericano profesional combinando hooks `SessionStart` (contexto base) y `UserPromptSubmit` (refuerzo continuo sin degradación).
   - **Desglose arquitectónico:** [`06-analisis-casos-reales-y-proyectos-bypabloc.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/06-analisis-casos-reales-y-proyectos-bypabloc.md).

---

## 📚 Índice de Documentos de la Investigación

La investigación se organiza en 9 documentos técnicos especializados disponibles en esta carpeta:

| # | Documento | Resumen Técnico |
| :---: | :--- | :--- |
| **01** | [`01-panorama-y-arquitectura-plugins.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/01-panorama-y-arquitectura-plugins.md) | Modelo conceptual de plugins vs `.claude/`, huella de tokens (*context footprint*), ámbitos de instalación (`user`, `project`, `local`, `managed`), ciclo de vida y tiers de marketplaces. |
| **02** | [`02-anatomia-y-especificacion-de-archivos.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/02-anatomia-y-especificacion-de-archivos.md) | Estructura de directorios estándar, especificación campo por campo de `plugin.json`, nombres reservados, reglas de rutas (`./`), contención y variables de entorno del runtime (`${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`). |
| **03** | [`03-guia-paso-a-paso-creacion-y-desarrollo.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/03-guia-paso-a-paso-creacion-y-desarrollo.md) | Tutorial paso a paso para crear un plugin desde cero, migración de carpetas `.claude/` existentes, carga local sin marketplace (`--plugin-dir`), monorepos y flujo de recarga rápida con `/reload-plugins`. |
| **04** | [`04-componentes-a-fondo.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/04-componentes-a-fondo.md) | Desglose profundo de cada primitiva: Skills (`SKILL.md`), Subagentes (`agents/*.md`), Hooks (`hooks.json` con `SessionStart`, `UserPromptSubmit`, etc.), Servidores MCP (`.mcp.json`), Servidores LSP, Monitores en background, Binarios en `bin/` y `userConfig`. |
| **05** | [`05-marketplaces-distribucion-y-publicacion.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/05-marketplaces-distribucion-y-publicacion.md) | Especificación de `marketplace.json`, fuentes (`github`, `relative`, `git-subdir`, `npm`, `archive`), modo `strict`, comandos CLI (`marketplace add/list/update`), etiquetado de versiones (`claude plugin tag`) y gobernanza corporativa. |
| **06** | [`06-analisis-casos-reales-y-proyectos-bypabloc.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/06-analisis-casos-reales-y-proyectos-bypabloc.md) | Análisis arquitectónico de los proyectos del usuario: `claude-plugins` (monorepo hub) y `claude-plugin-spanish-latam` (inyección dual con `SessionStart` y `UserPromptSubmit`, reglas, CJS y tests unitarios). Comparativa con plugins oficiales de Anthropic. |
| **07** | [`07-tips-comunidad-mejores-practicas-y-antipatrones.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/07-tips-comunidad-mejores-practicas-y-antipatrones.md) | Consejos clave 2025-2026: optimización de context window, `disable-model-invocation`, los 5 antipatrones comunes (componentes dentro de `.claude-plugin/`, `CLAUDE.md` inútil en la raíz, cuoteo de rutas), gestión de dependencias en `${CLAUDE_PLUGIN_DATA}` y Git worktrees. |
| **08** | [`08-recetario-de-ejemplos-practicos.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/08-recetario-de-ejemplos-practicos.md) | 5 plantillas completas listas para producción: (1) Quality Gate & Auto-formateo en `PostToolUse`, (2) Auditoría de Seguridad FinTech, (3) Servidor MCP con `userConfig` seguro, (4) Monitor de logs en background, (5) Validador de ramas y commits en `bin/`. |
| **09** | [`09-testing-evals-debugging-y-troubleshooting.md`](file:///home/bypabloc/projects/bypabloc/laya/docs/research/claude_plugins/09-testing-evals-debugging-y-troubleshooting.md) | Validación estática con `claude plugin validate --strict`, suite de evaluaciones de comportamiento con `claude plugin eval`, depuración con `/plugin` y `claude --debug`, y matriz de resolución de errores comunes. |

---

## ⚡ Tarjeta de Referencia Rápida (Cheat Sheet)

### Comandos de Terminal (`claude plugin`)
```bash
# Crear scaffolding de plugin local
claude plugin init mi-plugin --with skills hooks

# Validar estricto antes de commitear
claude plugin validate ./mi-plugin --strict

# Ejecutar una sesión de prueba cargando el plugin
claude --plugin-dir ./mi-plugin

# Cargar un monorepo con múltiples plugins
claude --plugin-dir ~/projects/bypabloc/claude-plugins/plugins

# Gestionar marketplaces
claude plugin marketplace add bypabloc/claude-plugins --scope user
claude plugin marketplace list
claude plugin marketplace update bypabloc

# Instalar, habilitar y desinstalar
claude plugin install spanish-latam-style@bypabloc --scope user
claude plugin enable spanish-latam-style
claude plugin disable spanish-latam-style
claude plugin uninstall spanish-latam-style --keep-data

# Crear release etiquetada con Git tag
claude plugin tag plugins/spanish-latam-style --push

# Ejecutar evaluaciones de comportamiento
claude plugin eval ./mi-plugin --threshold 0.9 --runs 3
```

### Comandos en Sesión Interactiva (`/`)
```text
/plugin                     # Abre el explorador visual de plugins (Discover, Installed, Errors)
/reload-plugins             # Recarga en caliente los cambios de skills, subagentes y hooks
/reload-plugins --force     # Fuerza recarga invalidando caché si cambiaron herramientas MCP o LSP
/<plugin-name>:<skill>      # Ejecuta un skill provisto por un plugin
@agent-<plugin-name>:<subagent> # Invoca a un subagente especializado
```

### Variables de Entorno del Runtime
- **`${CLAUDE_PLUGIN_ROOT}`:** Ruta absoluta de instalación del plugin (inmutable tras updates).
- **`${CLAUDE_PLUGIN_DATA}`:** `~/.claude/plugins/data/<id>/` (persiste tras updates; almacenar `node_modules`, venv y cachés).
- **`${CLAUDE_PROJECT_DIR}`:** Raíz del proyecto del usuario en ejecución.
- **`CLAUDE_PLUGIN_OPTION_<KEY>`:** Variable de entorno exportada a hooks para opciones de `userConfig`.
