# Marketplaces, Distribución y Publicación de Plugins

> Guía técnica integral sobre cómo estructurar catálogos de plugins (`marketplace.json`), fuentes de distribución (Git, npm, URLs, archivos locales), políticas corporativas de allowlist y flujo de versionado/publicación.

---

## 1. Arquitectura de un Marketplace en Claude Code

Un **marketplace** en Claude Code no es una tienda web alojada, sino un repositorio Git, directorio local o endpoint HTTP que contiene un catálogo declarativo denominado `.claude-plugin/marketplace.json`.

```
+-----------------------------------------------------------------------------------+
|                        Marketplace Repository (Git / Monorepo)                    |
+-----------------------------------------------------------------------------------+
|  .claude-plugin/                                                                  |
|  └── marketplace.json            <-- Catálogo con la lista de plugins y fuentes   |
|                                                                                   |
|  plugins/                        <-- Subdirectorios con plugins independientes    |
|  ├── plugin-a/                                                                    |
|  │   ├── .claude-plugin/plugin.json                                               |
|  │   └── skills/                                                                  |
|  └── plugin-b/                                                                    |
|      ├── .claude-plugin/plugin.json                                               |
|      └── hooks/                                                                   |
+-----------------------------------------------------------------------------------+
```

---

## 2. Especificación Completa de `marketplace.json`

### Ejemplo del Catálogo: `.claude-plugin/marketplace.json`

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "empresa-tools",
  "description": "Catálogo oficial de herramientas y plugins de ingeniería",
  "owner": {
    "name": "Equipo de Infraestructura",
    "email": "devops@empresa.com",
    "url": "https://empresa.com"
  },
  "metadata": {
    "pluginRoot": "./plugins"
  },
  "plugins": [
    {
      "name": "code-standards",
      "displayName": "Estándares y Linters",
      "description": "Aplica linters automáticos y validaciones de tipos en cada commit",
      "version": "1.2.0",
      "category": "development",
      "tags": ["linter", "typescript", "standards"],
      "source": "./plugins/code-standards",
      "homepage": "https://github.com/empresa/code-standards"
    },
    {
      "name": "deploy-helper",
      "displayName": "Deployment Assistant",
      "description": "Comandos de despliegue y monitoreo de pipelines",
      "version": "2.0.1",
      "category": "devops",
      "source": {
        "source": "github",
        "repo": "empresa/deploy-plugin",
        "ref": "v2.0.1",
        "sha": "4a5b6c7d8e9f0123456789abcdef0123456789ab"
      }
    },
    {
      "name": "legacy-migrator",
      "description": "Herramienta descargada desde un subdirectorio en monorepo externo",
      "source": {
        "source": "git-subdir",
        "url": "https://github.com/empresa/core-monorepo.git",
        "path": "tools/claude-migrator",
        "ref": "main"
      }
    }
  ]
}
```

### Tipos de Fuentes de Plugins (`plugin.source`)

| Tipo | Sintaxis / Propiedades Clave | Caso de Uso |
| :--- | :--- | :--- |
| **Ruta Relativa** | `"./plugins/mi-plugin"` o bare name con `metadata.pluginRoot` | Plugins alojados dentro del mismo repositorio del marketplace (patrón monorepo). |
| **`github`** | `repo: "owner/repo"`, `ref: "v1.0"`, `sha: "40-hex"` | Repositorios públicos o privados de GitHub con control estricto de commit SHA. |
| **`url`** | `url: "https://gitlab.com/group/repo.git"`, `ref: "main"` | Cualquier servidor Git (GitLab, Bitbucket, Azure DevOps o servidores privados). |
| **`git-subdir`** | `url: "..."`, `path: "sub/carpeta"`, `ref: "..."` | Extraer únicamente una subcarpeta de un monorepo Git mediante *sparse partial clone*. |
| **`npm`** | `package: "@scope/pkg"`, `version: "^1.0.0"`, `registry: "..."` | Plugins empaquetados y distribuidos mediante registros npm privados o públicos. |
| **`archive`** | `url: "https://.../pkg.zip"`, `sha256: "64-hex"` | Archivos ZIP inmutables descargados por HTTPS con validación criptográfica de hash. |
| **`command`** | `command: "cli-tool get-plugin-dir"`, `timeout: 60`, `mode: "copy"` | Directorio generado dinámicamente por un ejecutable local (ej. tooling de IDEs). |

---

## 3. Modo Estricto (`strict`) y Fusión de Manifiestos

Cuando Claude Code descarga un plugin, este puede contener su propio `.claude-plugin/plugin.json`, mientras que la entrada del marketplace también puede especificar componentes. El campo booleano `strict` en la entrada del marketplace controla cómo se resuelven estas discrepancias:

| `strict` | ¿Existe `plugin.json` en el plugin? | ¿La entrada del marketplace declara componentes? | Comportamiento |
| :--- | :--- | :--- | :--- |
| `true` (default) | Sí | Sí | **Fusión jerárquica:** `plugin.json` es la autoridad principal. Los `skills`, `agents` y `commands` del marketplace se agregan a los existentes. Los `hooks` del marketplace reemplazan los del manifiesto si comparten el mismo evento. |
| `false` | Sí | Sí | **Conflicto bloqueante:** Falla la instalación con error `Plugin has conflicting manifests`. Evita sobreescrituras no intencionales. |
| Cualquiera | No | Sí | La entrada del marketplace actúa formalmente como el manifiesto definitivo del plugin. |

---

## 4. Comandos de Gestión de Marketplaces en CLI

### 1. Registrar un Marketplace (`marketplace add`)
Permite registrar repositorios remotos o directorios locales:

```bash
# Agregar desde GitHub
claude plugin marketplace add bypabloc/claude-plugins --scope user

# Agregar desde un repositorio Git corporativo
claude plugin marketplace add https://gitlab.empresa.com/ia/plugins.git --scope project

# Agregar desde una carpeta local en desarrollo
claude plugin marketplace add /home/bypabloc/projects/bypabloc/claude-plugins --scope local
```

### 2. Listar y Actualizar
```bash
# Listar marketplaces registrados
claude plugin marketplace list

# Actualizar el catálogo descargando la última versión del ref
claude plugin marketplace update bypabloc
```

### 3. Instalar un Plugin Específico desde un Marketplace
```bash
# Sintaxis: <plugin-name>@<marketplace-name>
claude plugin install spanish-latam-style@bypabloc --scope user
```

---

## 5. Versionado y Creación de Releases con `claude plugin tag`

Claude Code incluye una herramienta automatizada para crear tags de Git consistentes para releases de plugins:

```bash
claude plugin tag plugins/spanish-latam-style --push
```

El comando realiza las siguientes verificaciones previas:
1. Comprueba que el árbol de trabajo de Git esté limpio (sin cambios sin commitear).
2. Valida que el `version` declarado en `plugin.json` coincida con el `version` del `marketplace.json`.
3. Crea un tag anotado con la convención estándar: `<nombre-plugin>--v<version>` (ej. `spanish-latam-style--v1.0.0`).
4. Lo envía al remote configurado (`git push origin <tag>`).

---

## 6. Publicación en el Directorio Oficial de Anthropic

Para someter un plugin a consideración en el catálogo oficial de Anthropic (`claude-plugins-official`), el manifiesto debe cumplir con requisitos específicos adicionales de metadatos y seguridad:

```json
{
  "name": "enterprise-auditor",
  "version": "1.0.0",
  "description": "Auditor estricto de código y seguridad para equipos empresariales",
  "icon": "./assets/icon.png",
  "documentationUrl": "https://docs.empresa.com/claude-plugin",
  "supportUrl": "https://soporte.empresa.com",
  "privacyPolicyUrl": "https://empresa.com/privacy",
  "termsOfServiceUrl": "https://empresa.com/terms"
}
```

### Checklist Obligatorio para Catálogo Oficial:
- [ ] Validación estricta con `claude plugin validate <path> --strict` sin ninguna advertencia.
- [ ] No incluir secretos hardcodeados ni credenciales en scripts o servidores MCP.
- [ ] Documentación clara de permisos y dependencias de sistema.
- [ ] Nombre único en `kebab-case` sin palabras reservadas (`claude-`, `anthropic-`).
- [ ] Licencia SPDX declarada (ej. `MIT`, `Apache-2.0`).

---

## 7. Políticas Corporativas y Marketplaces Privados (Enterprise Governance)

Los administradores de sistemas pueden controlar exhaustivamente qué plugins y marketplaces están permitidos en los equipos mediante `settings.json` administrado:

```json
{
  "strictKnownMarketplaces": [
    { "source": "github", "repo": "empresa-segura/*" },
    { "source": "hostPattern", "hostPattern": "^git\\.empresa\\.internal$" },
    { "source": "skills-dir" }
  ],
  "blockedMarketplaces": [
    { "source": "hostPattern", "hostPattern": ".*untrusted.*" }
  ],
  "disableSideloadFlags": true
}
```

- **`strictKnownMarketplaces`:** Actúa como lista blanca (allowlist). Si se define, **ningún marketplace fuera de la lista puede ser añadido**.
- **`disableSideloadFlags`:** Deshabilita el uso de `--plugin-dir` y `--plugin-url`, impidiendo que los desarrolladores ejecuten código arbitrario sin homologación previa.
- **`skills-dir`:** Permite explícitamente que los desarrolladores conserven sus plugins locales en `~/.claude/skills/`.
