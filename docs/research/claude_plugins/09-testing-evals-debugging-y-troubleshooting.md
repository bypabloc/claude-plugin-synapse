# Testing, Evaluaciones (Evals), Depuración y Resolución de Problemas

> Guía metodológica para la validación estricta, suite de pruebas automatizadas con `claude plugin eval`, diagnóstico en sesión y resolución de errores comunes en plugins.

---

## 1. Validación Estática Automatizada (`claude plugin validate`)

El validador de Claude Code inspecciona el manifiesto `plugin.json`, el frontmatter de todos los skills y agentes, la sintaxis de hooks y la consistencia de servidores MCP y LSP.

```bash
claude plugin validate ./mi-plugin --strict
```

### Códigos de Salida del Validador

| Código | Veredicto | Significado |
| :---: | :--- | :--- |
| **`0`** | `Validation passed` | Manifiesto y componentes válidos. Con `--strict`, cero advertencias. |
| **`1`** | `Validation failed` | Error de esquema, ruta rota o advertencia en modo estricto. |
| **`2`** | `Unexpected error` | Error de I/O o directorio inaccesible. |

### Integración en CI/CD (GitHub Actions)
Ejemplo de workflow para validar plugins antes de fusionar pull requests:

```yaml
name: Validate Plugin
on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install Node.js
        uses: actions/setup-node@v4
        with:
          node-version: 20
      - name: Install Claude Code CLI
        run: npm install -g @anthropic-ai/claude-code
      - name: Run Plugin Validation
        run: claude plugin validate . --strict
```

---

## 2. Sistema Oficial de Evaluaciones de Comportamiento (`claude plugin eval`)

Validar que el JSON no tenga errores de sintaxis no garantiza que el plugin guíe efectivamente las decisiones de Claude. Claude Code incluye el subcomando `claude plugin eval` (introducido en v2.1.269) para realizar pruebas de comportamiento reproducibles con y sin el plugin (estudio de ablación).

```
+-----------------------------------------------------------------------------------+
|                            Claude Code Plugin Eval Loop                           |
+-----------------------------------------------------------------------------------+
|  1. Ejecuta caso de prueba SIN el plugin (Línea Base / Control)                   |
|  2. Ejecuta caso de prueba CON el plugin (Tratamiento)                            |
|  3. Un modelo juez evalúa si el comportamiento cumplió con los criterios         |
|  4. Compara el puntaje frente al umbral (--threshold 0.95)                       |
+-----------------------------------------------------------------------------------+
```

### Inicialización de una Suite de Evaluaciones
Ejecutar desde la raíz del plugin:

```bash
claude plugin eval init calidades-codigo
```
Claude iniciará una sesión interactiva que:
1. Lee tu plugin.
2. Te pregunta qué comportamientos específicos debe garantizar.
3. Escribe los archivos de casos en `evals/`.
4. Ejecuta una prueba piloto y calibra los criterios del modelo juez.

### Estructura de un Caso de Evaluación (`evals/caso-01/`)
```text
evals/
└── pr-review-test/
    ├── prompt.md             <-- Entrada simulada del usuario
    └── criteria.md           <-- Rúbrica en lenguaje natural para el juez LLM
```

Contenido de `evals/pr-review-test/criteria.md`:
```markdown
El asistente debe:
1. Rechazar la inclusión de console.log en archivos de producción.
2. Notificar que falta cobertura de pruebas unitarias para la función `validateCard`.
3. Comunicarse en español neutro latinoamericano con tuteo profesional sin voseo.
```

### Ejecución de Evaluaciones en CI
```bash
claude plugin eval . \
  --threshold 0.9 \
  --runs 3 \
  --concurrency 2 \
  --trust-plugin \
  --json ./tmp/eval-results.json
```

Si el promedio de las corridas con el plugin no supera el umbral (`threshold`), el comando sale con código `1`, bloqueando el despliegue de plugins regresivos en CI.

---

## 3. Depuración en Sesión Interactiva

Cuando un plugin no se comporta según lo previsto, sigue esta secuencia de diagnóstico dentro de Claude Code:

### 1. Panel Visual de Plugins (`/plugin`)
- **Pestaña Installed:** Muestra el plugin activo y cada componente individual detectado (skills, subagentes, hooks, servidores MCP).
- **Pestaña Errors:** Si un componente falló al cargar (ej. ruta inexistente o JSON malformado), muestra el error específico de forma destacada.

### 2. Recarga en Caliente (`/reload-plugins`)
- Aplica los cambios realizados en el disco inmediatamente.
- Si modificaste servidores MCP o LSP, utiliza `/reload-plugins --force` para reconstruir la lista de herramientas e invalidar la caché de prompts.

### 3. Registro Detallado (`claude --debug`)
Inicia Claude Code con el flag `--debug` para ver en la terminal o en los logs de depuración:
- Qué hooks se activaron y con qué código de salida terminaron.
- La salida de `stderr` de los servidores LSP y MCP.
- Las variables de entorno resueltas (`${CLAUDE_PLUGIN_ROOT}`).

---

## 4. Matriz de Errores Comunes y Soluciones

| Mensaje de Error / Síntoma | Causa Raíz | Solución Inmediata |
| :--- | :--- | :--- |
| `commands path not found` o `skills path not found` | Una clave de ruta en `plugin.json` apunta a un directorio que no existe. | Verifica que la carpeta exista o elimina la clave del manifiesto para usar el auto-descubrimiento predeterminado. |
| `Default <folder>/ folder is ignored because manifest sets "<key>"` | El plugin tiene la carpeta estándar (ej. `skills/`) pero el manifiesto declara `skills: ["./custom/"]`. | Si declaras rutas explícitas, incluye la carpeta estándar: `"skills": ["./skills/", "./custom/"]`. |
| `Path contains ".." which could be a path traversal attempt` | Una ruta en el manifiesto intenta salir de la raíz del plugin (ej. `../scripts`). | Todas las rutas deben comenzar con `./` y resolver estrictamente dentro del directorio del plugin. |
| `Plugin name "<name>" is reserved: it passes as one of Anthropic's own` | El nombre del plugin comienza con `claude-`, `anthropic-` o usa palabras reservadas. | Renombra el plugin a un identificador propio en `kebab-case` (ej. `mi-empresa-linter`). |
| `The plugin loads but its skills are missing` | La carpeta `skills/` fue colocada por error dentro de `.claude-plugin/`. | Mueve `skills/` a la raíz del plugin. Solo `plugin.json` debe vivir dentro de `.claude-plugin/`. |
| `Hook command references user config: ${user_config.KEY}` | Un hook en modo shell intentó interpolar una opción de usuario directamente en `command`. | Usa la variable de entorno segura `CLAUDE_PLUGIN_OPTION_<KEY>` dentro del script o utiliza la sintaxis desacoplada `args`. |
| `Plugin has conflicting manifests` | El marketplace define componentes y tiene `strict: false`, colisionando con el `plugin.json` interno. | Establece `"strict": true` en el marketplace para permitir fusión jerárquica o elimina componentes duplicados. |
| `LSP server disconnected unexpectedly` | El servidor LSP imprimió logs o texto plano en `stdout`. | Redirige todos los logs y mensajes de depuración a `stderr`. `stdout` está reservado exclusivamente para mensajes JSON-RPC. |
