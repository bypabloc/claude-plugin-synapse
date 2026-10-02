# Análisis de Proyectos de Referencia: bypabloc y Plugins Oficiales

> Desglose de ingeniería inversa y patrones arquitectónicos aplicados en los proyectos reales del usuario (`claude-plugins`, `claude-plugin-spanish-latam`) y su contraste con los plugins oficiales de Anthropic.

---

## 1. Desglose del Monorepo de Marketplaces: `claude-plugins`

- **Ruta de proyecto:** [`~/projects/bypabloc/claude-plugins`](file:///home/bypabloc/projects/bypabloc/claude-plugins)
- **Repositorio local:** `/home/bypabloc/projects/bypabloc/claude-plugins`

El proyecto implementa el patrón **Marketplace Monorepo Hub**, ideal para gestionar un catálogo de múltiples plugins dentro de un único repositorio Git.

### Estructura del Proyecto
```text
claude-plugins/
├── .claude-plugin/
│   └── marketplace.json      <-- Registro central del catálogo
├── plugins/
│   └── spanish-latam-style/  <-- Plugin empaquetado en subdirectorio
│       ├── .claude-plugin/plugin.json
│       ├── commands/
│       ├── hooks/
│       ├── rules/
│       ├── scripts/
│       ├── skills/
│       └── tests/
└── README.md
```

### Manifiesto del Catálogo (`.claude-plugin/marketplace.json`)
```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "bypabloc",
  "description": "Marketplace oficial de plugins de Claude Code por bypabloc",
  "owner": {
    "name": "Pablo Contreras",
    "email": "pacg1991@gmail.com"
  },
  "plugins": [
    {
      "name": "spanish-latam-style",
      "description": "Aplica y refuerza el uso estricto de español neutro latinoamericano estándar (tuteo profesional, sin voseo ni modismos regionales) en interacciones, documentación, commits y código en Claude Code.",
      "version": "1.0.0",
      "author": {
        "name": "Pablo Contreras",
        "email": "pacg1991@gmail.com"
      },
      "category": "development",
      "source": "./plugins/spanish-latam-style",
      "homepage": "https://github.com/bypabloc/claude-plugin-spanish-latam"
    }
  ]
}
```

### Ventajas Técnicas del Patrón:
1. **Resolución Relativa Inmediata:** La fuente `"source": "./plugins/spanish-latam-style"` permite que cualquier usuario que agregue el marketplace (`claude plugin marketplace add bypabloc/claude-plugins`) descargue e instale el plugin sin requerir llamadas de red a repositorios adicionales.
2. **Cohesión de Pruebas:** Permite ejecutar suites de validación (`claude plugin validate .`) para auditar simultáneamente el catálogo y los plugins contenidos.

---

## 2. Desglose del Plugin de Dialecto y Calidad: `claude-plugin-spanish-latam`

- **Ruta de proyecto:** [`~/projects/bypabloc/claude-plugin-spanish-latam`](file:///home/bypabloc/projects/bypabloc/claude-plugin-spanish-latam)
- **Repositorio local:** `/home/bypabloc/projects/bypabloc/claude-plugin-spanish-latam`

El proyecto es un ejemplo de nivel de producción de cómo moldear de forma determinista el comportamiento, tono y estilo lingüístico de Claude Code.

### Arquitectura de Componentes
```text
claude-plugin-spanish-latam/
├── .claude-plugin/
│   └── plugin.json                  <-- Identidad, versión y keywords
├── marketplace-entry.json           <-- Plantilla para inclusión en marketplaces remotos
├── rules/
│   └── language-style.md            <-- Especificación canónica de reglas de idioma
├── hooks/
│   └── hooks.json                   <-- Orquestación de eventos SessionStart y UserPromptSubmit
├── scripts/
│   ├── session-start.cjs            <-- Inyector de contexto al inicio
│   ├── user-prompt-submit.cjs       <-- Inyector de refuerzo en cada turno
│   ├── check_spanish_style.py       <-- Script linter de modismos prohibidos
│   └── install_rule.py              <-- Instalador de regla en .claude/rules/ local
├── skills/
│   ├── spanish-latam-style/SKILL.md <-- Activación y directrices completas
│   └── audit-spanish-style/SKILL.md <-- Comando de auditoría de archivos
├── commands/                        <-- Compatibilidad con clientes clásicos
│   ├── audit-spanish.md
│   ├── format-spanish.md
│   └── install-rule.md
└── tests/                           <-- Validación automatizada con Python unittest
    ├── test_manifest.py
    ├── test_hook.py
    └── test_linter.py
```

### Patrón de Inyección Dual (Dual-Hook Context Pattern)

Uno de los aportes más sólidos de esta implementación es la solución al problema del **"Dialect Mirroring" (Espejo Dialectal)**, donde los LLMs tienden a imitar los modismos o voseo del usuario.

El plugin neutraliza este comportamiento mediante dos hooks complementarios en `hooks/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/scripts/session-start.cjs\"",
            "timeout": 10
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/scripts/user-prompt-submit.cjs\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

#### 1. Hook `SessionStart` (`scripts/session-start.cjs`)
Carga de forma transparente el archivo de reglas completo `rules/language-style.md` y lo inyecta como contexto base de la sesión:

```javascript
const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, '..');
const ruleFile = path.join(pluginRoot, 'rules', 'language-style.md');

let content = fs.existsSync(ruleFile) ? fs.readFileSync(ruleFile, 'utf8') : '';
if (!content) {
  content = "IDIOMA Y ESTILO OBLIGATORIO: Comunícate siempre en español neutro latinoamericano estándar...";
}

const output = {
  hookSpecificOutput: {
    hookEventName: 'SessionStart',
    additionalContext: content
  }
};
process.stdout.write(JSON.stringify(output) + '\n');
```

#### 2. Hook `UserPromptSubmit` (`scripts/user-prompt-submit.cjs`)
Inyecta un recordatorio perentorio antes de que el LLM procese la solicitud del usuario, forzando la resistencia al espejo dialectal:

```javascript
const output = {
  systemMessage: "IDIOMA Y ESTILO: Comunícate estrictamente en español neutro latinoamericano estándar con tuteo profesional (tú). Prohibido terminantemente el voseo (vos, tenés, podés, mirá, hacé, decime, avisame) y giros peninsulares (fichero, ordenador, vale). Si el usuario utiliza voseo o modismos locales, aplica el principio de resistencia al espejo dialectal y mantén tuteo estándar neutro sin imitar su dialecto."
};
process.stdout.write(JSON.stringify(output) + '\n');
```

---

## 3. Pruebas Automatizadas en Plugins (`tests/test_manifest.py`)

El proyecto incluye pruebas unitarias basadas en `unittest` de Python para garantizar la integridad estructural antes de commitear:

```python
class TestPluginManifest(unittest.TestCase):
    def setUp(self):
        self.plugin_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.manifest_path = os.path.join(self.plugin_root, ".claude-plugin", "plugin.json")

    def test_manifest_exists_and_valid_json(self):
        self.assertTrue(os.path.isfile(self.manifest_path))
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["name"], "spanish-latam-style")
        self.assertIn("version", data)

    def test_skills_exist(self):
        latam_skill = os.path.join(self.plugin_root, "skills", "spanish-latam-style", "SKILL.md")
        self.assertTrue(os.path.isfile(latam_skill))

    def test_hooks_exist(self):
        hooks_path = os.path.join(self.plugin_root, "hooks", "hooks.json")
        self.assertTrue(os.path.isfile(hooks_path))
```

Este enfoque previene regresiones silenciosas (como olvidar agregar `SKILL.md` o corromper el JSON del hook) antes de ejecutar `claude plugin validate`.

---

## 4. Comparativa con Plugins Oficiales de Anthropic

| Dimensión | `claude-plugin-spanish-latam` (Bypabloc) | `security-guidance` (Anthropic) | `pr-review-toolkit` (Anthropic) |
| :--- | :--- | :--- | :--- |
| **Objetivo** | Gobernanza de lenguaje y estilo de comunicación. | Detección proactiva de vulnerabilidades de código. | Revisión distribuida de PRs con múltiples agentes. |
| **Mecanismo Primario** | Hooks (`SessionStart` + `UserPromptSubmit`) + Reglas Markdown. | Subagentes (`security-auditor`) + Hooks `PostToolUse`. | Subagentes especializados (`pr-test-analyzer`, etc.). |
| **Herramientas Externas** | Scripts en Node.js y linters en Python. | Ninguna (razonamiento puro sobre AST/Diffs). | Ninguna (orquestación pura de subagentes). |
| **Invocación** | Siempre activa (autónoma en background) + comandos manuales. | Activa tras ediciones de archivos sensibles. | Manual vía `/code-review` o `@agent`. |
