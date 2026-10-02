# Tips de la Comunidad, Mejores Prácticas y Antipatrones

> Compilación de lecciones aprendidas, consejos prácticos de la comunidad de desarrolladores (2025-2026), optimización del context window y trampas comunes al crear plugins para Claude Code.

---

## 1. Consejos de la Comunidad y Optimización del Context Window

### 1. Higiene de Contexto y Gestión del "Token Footprint"
Cada plugin habilitado añade una carga base fija a todas las sesiones, independientemente de si se utiliza o no.
- **Descripciones Claras y Concisas:** La `description` en el frontmatter de un skill o subagente debe responder a dos preguntas en máximo dos oraciones: *¿Qué hace?* y *¿Cuándo debe usarse?*. Descripciones extensas saturan la ventana de contexto.
- **Uso Estratégico de `disable-model-invocation: true`:** Si un skill está destinado únicamente a ser ejecutado por el desarrollador mediante slash command (ej. `/deploy`, `/format-all`, `/benchmark`), agrega `disable-model-invocation: true`. Esto evita que Claude evalúe innecesariamente el skill en cada turno de conversación.
- **Carga Diferida (Lazy Loading):** Los archivos de instrucciones completos (`SKILL.md`) solo se cargan en la ventana de contexto cuando el skill se invoca. Mantén la lógica pesada dentro del cuerpo del Markdown y deja el frontmatter liviano.

### 2. Auto-Descubrimiento vs. Declaración Explícita
Claude Code implementa un motor de auto-descubrimiento robusto:
- Si colocas archivos en `skills/`, `agents/`, `hooks/hooks.json` o `.mcp.json`, el runtime los detectará automáticamente sin necesidad de listarlos en `plugin.json`.
- **Recomendación de la comunidad:** Mantén `plugin.json` lo más limpio posible (solo metadatos, `userConfig` y dependencias). Usa las claves de rutas (`skills: [...]`, `agents: [...]`) únicamente cuando necesites apuntar a directorios no estándar.

### 3. Aislamiento con Git Worktrees en Subagentes
Cuando un subagente realiza tareas masivas de refactorización o auditoría, define `isolation: "worktree"` en su frontmatter:

```markdown
---
name: refactor-agent
isolation: "worktree"
---
```
Esto fuerza a Claude Code a clonar un worktree temporal aislado de Git, garantizando que los archivos modificados no colisionen con el árbol de trabajo principal del desarrollador hasta que los cambios sean formalmente aprobados.

---

## 2. Antipatrones y Errores Fatales Comunes

### Antipatrón 1: Ubicar Componentes Dentro de `.claude-plugin/`
```text
INCORRECTO (No carga):
mi-plugin/
└── .claude-plugin/
    ├── plugin.json
    ├── skills/          <-- NUNCA colocar aquí
    └── hooks.json       <-- NUNCA colocar aquí

CORRECTO:
mi-plugin/
├── .claude-plugin/
│   └── plugin.json      <-- Único archivo permitido aquí
├── skills/
└── hooks/
    └── hooks.json
```
**Síntoma:** El comando `claude plugin list` indica que el plugin cargó, pero `/plugin` muestra 0 skills y 0 hooks detectados.

---

### Antipatrón 2: Incluir `CLAUDE.md` en la Raíz del Plugin
Colocar un archivo `CLAUDE.md` en la raíz de un plugin es inútil:
- Claude Code **ignora** cualquier `CLAUDE.md` que resida dentro de un plugin y el validador `claude plugin validate` emitirá una advertencia (`CLAUDE.md at the plugin root is not loaded as project context`).
- **Solución correcta:** Si tu plugin necesita inyectar instrucciones generales obligatorias para todo el proyecto, utiliza un hook en `SessionStart` (como hace `claude-plugin-spanish-latam`) o distribuye la instrucción como un skill en `skills/<nombre>/SKILL.md`.

---

### Antipatrón 3: Olvidar Comillas en Rutas Shell (`${CLAUDE_PLUGIN_ROOT}`)
En sistemas operativos Linux, macOS o WSL, si la ruta del usuario contiene espacios (ej. `/home/usuario/My Projects/...`), el comando fallará con errores de sintaxis si no está cuoteado:

```json
// INCORRECTO (Falla si hay espacios en la ruta):
{
  "type": "command",
  "command": "${CLAUDE_PLUGIN_ROOT}/scripts/format.sh"
}

// CORRECTO:
{
  "type": "command",
  "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/format.sh\""
}
```

---

### Antipatrón 4: Escribir Estado Mutable dentro de `${CLAUDE_PLUGIN_ROOT}`
- El directorio `${CLAUDE_PLUGIN_ROOT}` apunta a una versión inmutable en caché de la instalación del plugin.
- Cuando el plugin se actualiza (`claude plugin update`), Claude Code descarga la nueva versión en un directorio de caché diferente y **elimina la versión antigua**. Cualquier archivo de log, base de datos SQLite o configuración guardada allí se perderá.
- **Solución correcta:** Escribir siempre el estado mutable, cachés y dependencias dentro de `${CLAUDE_PLUGIN_DATA}` (`~/.claude/plugins/data/<plugin-id>/`).

---

### Antipatrón 5: Intentar Usar Secretos en Comandos Shell Directos
Por diseño de seguridad, Claude Code **rechaza terminantemente** referencias a `${user_config.*}` dentro de campos `command` de hooks o monitores que se ejecutan a través de un shell, para evitar inyecciones de comandos maliciosas.

```json
// INCORRECTO (Error en validación):
{
  "command": "curl -H \"Authorization: ${user_config.api_token}\" https://api.com"
}

// CORRECTO (Vía variable de entorno segura):
// El script de destino lee la variable de entorno CLAUDE_PLUGIN_OPTION_API_TOKEN
{
  "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/sync.sh\""
}
```

---

## 3. Patrón de Gestión de Dependencias en `${CLAUDE_PLUGIN_DATA}`

Si tu plugin requiere dependencias de Node.js (`node_modules`) o un entorno virtual de Python (`.venv`), no debes empaquetar carpetas gigantes dentro del repositorio Git del plugin.

Aplica el **Patrón de Auto-Instalación en SessionStart**:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "diff -q \"${CLAUDE_PLUGIN_ROOT}/package.json\" \"${CLAUDE_PLUGIN_DATA}/package.json\" >/dev/null 2>&1 || (cd \"${CLAUDE_PLUGIN_DATA}\" && cp \"${CLAUDE_PLUGIN_ROOT}/package.json\" . && npm install --silent) || true"
          }
        ]
      }
    ]
  }
}
```

1. Compara el `package.json` empaquetado en el plugin con el que reside en `${CLAUDE_PLUGIN_DATA}`.
2. Si hubo cambios o es la primera vez que se ejecuta, copia el archivo y corre `npm install` dentro de `${CLAUDE_PLUGIN_DATA}`.
3. Tus servidores MCP o scripts pueden configurar su variable de entorno `NODE_PATH` apuntando a `${CLAUDE_PLUGIN_DATA}/node_modules`.

---

## 4. Mantenimiento y Limpieza del Entorno

1. **Monitoreo de Costos en Tokens:**
   Ejecuta periódicamente en tu terminal:
   ```bash
   claude plugin details <plugin-name>
   ```
   Revisa la sección `Projected token cost` para conocer el impacto de tokens fijos añadidos a cada prompt.

2. **Podar Dependencias Huérfanas (`prune`):**
   Si desinstalaste plugins que trajeron consigo dependencias automáticas, límpialas con:
   ```bash
   claude plugin prune --dry-run
   claude plugin prune -y
   ```

3. **Desinstalación Preservando Datos:**
   Si deseas reinstalar o probar una versión limpia sin borrar las credenciales o bases de datos locales:
   ```bash
   claude plugin uninstall <plugin-name> --keep-data
   ```
