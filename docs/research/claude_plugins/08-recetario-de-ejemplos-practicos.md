# Recetario de Ejemplos Prácticos de Creación de Plugins

> Colección de plantillas completas, funcionales y listas para producción que ilustran la creación de plugins para diversos casos de uso: calidad de código, seguridad FinTech, servidores MCP, monitoreo y personalización.

---

## Ejemplo 1: Plugin de Quality Gate & Auto-Formateo (`dev-quality-gate`)

**Caso de Uso:** Cada vez que Claude escribe o edita un archivo (`Write` o `Edit`), ejecuta automáticamente Prettier o Black para mantener el estilo de código del equipo impecable sin intervención manual.

### 1. Manifiesto: `dev-quality-gate/.claude-plugin/plugin.json`
```json
{
  "name": "dev-quality-gate",
  "version": "1.0.0",
  "description": "Formatea y valida archivos TypeScript y Python automáticamente tras cada edición",
  "author": {
    "name": "Equipo de Calidad",
    "email": "qa@empresa.com"
  },
  "license": "MIT"
}
```

### 2. Configuración de Hooks: `dev-quality-gate/hooks/hooks.json`
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/auto-format.sh\""
          }
        ]
      }
    ]
  }
}
```

### 3. Script Formateador: `dev-quality-gate/scripts/auto-format.sh`
```bash
#!/usr/bin/env bash
set -eo pipefail

# Leer la carga útil enviada por Claude Code en stdin
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')

if [ -z "$FILE_PATH" ] || [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

# Formateo según la extensión
case "$FILE_PATH" in
  *.ts|*.tsx|*.js|*.vue|*.json)
    if command -v npx >/dev/null 2>&1; then
      npx prettier --write "$FILE_PATH" >/dev/null 2>&1 || true
    fi
    ;;
  *.py)
    if command -v black >/dev/null 2>&1; then
      black --quiet "$FILE_PATH" >/dev/null 2>&1 || true
    fi
    ;;
esac

exit 0
```

---

## Ejemplo 2: Plugin de Auditoría de Seguridad FinTech (`fintech-security-guard`)

**Caso de Uso:** Audita código en busca de fugas de datos PII (RUT chileno, RFC mexicano, números de tarjeta PAN) y violaciones de seguridad PCI DSS.

### 1. Manifiesto: `fintech-security-guard/.claude-plugin/plugin.json`
```json
{
  "name": "fintech-security-guard",
  "version": "1.1.0",
  "description": "Auditor estricto de seguridad para cumplimiento FinTech y protección de PII",
  "author": {
    "name": "Pablo Contreras",
    "email": "pacg1991@gmail.com"
  },
  "keywords": ["fintech", "security", "pii", "pci-dss"]
}
```

### 2. Subagente Auditor: `fintech-security-guard/agents/auditor.md`
```markdown
---
name: auditor
description: Subagente especializado en auditoría de seguridad bancaria y FinTech. Úsalo tras modificar endpoints de pago o modelos de usuario.
model: sonnet
effort: high
tools:
  - ReadFile
  - Grep
  - Glob
disallowedTools:
  - WriteFile
  - EditFile
  - Bash
isolation: "worktree"
---

Eres el Auditor Principal de Cumplimiento PCI DSS y Ciberseguridad FinTech.
Inspecciona el código modificado y busca de forma exhaustiva:
1. **Datos de Tarjeta:** Almacenamiento o registro en logs de CVV/CVC, track data o PAN sin enmascarar.
2. **Validación Algorítmica:** Verifica que los identificadores fiscales (RUT chileno vía Módulo 11, RFC mexicano) no se validen solo por Regex, sino mediante chequeo algorítmico estricto.
3. **Inyecciones:** Queries SQL dinámicas no parametrizadas.
4. **Secretos Hardcodeados:** API keys de pasarelas de pago (Stripe, Transbank, MercadoPago) en texto plano.

Genera un reporte técnico conciso indicando archivo, línea y remediación obligatoria.
```

### 3. Skill de Auditoría Manual: `fintech-security-guard/skills/audit/SKILL.md`
```markdown
---
name: audit
description: Inicia una auditoría de seguridad FinTech sobre el código actual.
disable-model-invocation: true
---

Ejecuta el subagente `@agent-fintech-security-guard:auditor` para analizar todos los archivos modificados respecto a la rama principal (`git diff origin/main...HEAD`).

Muestra el resultado en una tabla con columnas: [Severidad], [Archivo:Línea], [Regla Viadala], [Solución Técnica].
```

---

## Ejemplo 3: Plugin con Servidor MCP y `userConfig` (`database-navigator`)

**Caso de Uso:** Conecta Claude Code a una base de datos PostgreSQL interna mediante un servidor MCP empaquetado en el plugin, solicitando la cadena de conexión de forma interactiva y segura.

### 1. Manifiesto: `database-navigator/.claude-plugin/plugin.json`
```json
{
  "name": "database-navigator",
  "version": "1.0.0",
  "description": "Inspección segura de esquemas de bases de datos mediante servidor MCP",
  "mcpServers": {
    "postgres-inspector": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/server/index.js"],
      "env": {
        "DATABASE_URL": "${user_config.db_connection_string}"
      }
    }
  },
  "userConfig": {
    "db_connection_string": {
      "type": "string",
      "title": "PostgreSQL Connection String",
      "description": "Cadena de conexión de solo lectura (ej. postgresql://user:pass@localhost:5432/db)",
      "sensitive": true,
      "required": true
    }
  }
}
```

### 2. Implementación del Servidor MCP: `database-navigator/server/index.js`
```javascript
#!/usr/bin/env node
const { Server } = require('@modelcontextprotocol/sdk/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { CallToolRequestSchema, ListToolsRequestSchema } = require('@modelcontextprotocol/sdk/types.js');
const { Client } = require('pg');

const dbUrl = process.env.DATABASE_URL;
const server = new Server(
  { name: 'postgres-inspector', version: '1.0.0' },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'list_tables',
      description: 'Lista todas las tablas públicas de la base de datos',
      inputSchema: { type: 'object', properties: {} }
    },
    {
      name: 'describe_table',
      description: 'Obtiene las columnas y tipos de datos de una tabla específica',
      inputSchema: {
        type: 'object',
        properties: { table_name: { type: 'string' } },
        required: ['table_name']
      }
    }
  ]
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const client = new Client({ connectionString: dbUrl });
  await client.connect();

  try {
    if (request.params.name === 'list_tables') {
      const res = await client.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
      );
      return { content: [{ type: 'text', text: JSON.stringify(res.rows, null, 2) }] };
    }

    if (request.params.name === 'describe_table') {
      const tableName = request.params.arguments.table_name;
      const res = await client.query(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = $1",
        [tableName]
      );
      return { content: [{ type: 'text', text: JSON.stringify(res.rows, null, 2) }] };
    }

    throw new Error(`Herramienta no encontrada: ${request.params.name}`);
  } finally {
    await client.end();
  }
});

async function run() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

run().catch((err) => {
  console.error('Error fatal en servidor MCP:', err);
  process.exit(1);
});
```

---

## Ejemplo 4: Plugin con Monitor de Logs en Background (`log-watcher`)

**Caso de Uso:** Inicia un observador persistente durante la sesión que notifica a Claude Code si ocurren errores críticos o excepciones no controladas en el servidor de desarrollo local.

### 1. Manifiesto: `log-watcher/.claude-plugin/plugin.json`
```json
{
  "name": "log-watcher",
  "version": "1.0.0",
  "description": "Monitoreo en tiempo real de logs de depuración locales",
  "experimental": {
    "monitors": "./monitors/monitors.json"
  }
}
```

### 2. Configuración del Monitor: `log-watcher/monitors/monitors.json`
```json
[
  {
    "name": "app-error-stream",
    "command": "tail -F ./tmp/logs/app.log 2>/dev/null | grep --line-buffered -E \"(ERROR|CRITICAL|Exception|Traceback)\"",
    "description": "Transmite errores y excepciones del backend local en tiempo real a Claude Code",
    "when": "always"
  }
]
```

---

## Ejemplo 5: Plugin de Utilidad CLI en `bin/` (`git-policy-enforcer`)

**Caso de Uso:** Inyecta un script binario en el `$PATH` de la herramienta Bash para verificar que los commits cumplan con la especificación Conventional Commits y las ramas sigan la convención `<tipo>/<nombre>`.

### 1. Manifiesto: `git-policy-enforcer/.claude-plugin/plugin.json`
```json
{
  "name": "git-policy-enforcer",
  "version": "1.0.0",
  "description": "Comandos de verificación de políticas de branching y commits"
}
```

### 2. Script Ejecutable: `git-policy-enforcer/bin/verify-branch-name`
```bash
#!/usr/bin/env bash
set -eo pipefail

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

if [ -z "$CURRENT_BRANCH" ] || [ "$CURRENT_BRANCH" = "HEAD" ]; then
  exit 0
fi

# Ramas fijas permitidas
if [[ "$CURRENT_BRANCH" =~ ^(master|main|dev|release)$ ]]; then
  echo "✔ Rama estándar principal: $CURRENT_BRANCH"
  exit 0
fi

# Formato obligatorio: tipo/nombre
if [[ ! "$CURRENT_BRANCH" =~ ^(feature|hotfix|bugfix|dev-[a-zA-Z0-9]+)/[a-zA-Z0-9_-]+$ ]]; then
  echo "✘ ERROR: Nombre de rama inválido: '$CURRENT_BRANCH'"
  echo "Regla obligatoria: Debe usar el formato '<tipo>/<nombre>' con barra divisoria '/'"
  echo "Ejemplos válidos: feature/payment-gateway, bugfix/fix-token, hotfix/critical-patch"
  exit 1
fi

echo "✔ Rama conforme a la convención: $CURRENT_BRANCH"
exit 0
```
*(No olvidar ejecutar `chmod +x git-policy-enforcer/bin/verify-branch-name`)*
