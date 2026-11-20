# Google Antigravity y Gemini CLI

> Antigravity (App 2.x, IDE y CLI `agy`) y Gemini CLI evaluados contra los 4 hooks de Synapse. **Desde el 19 de mayo de 2026 Gemini CLI se integró en Antigravity**: Antigravity es el destino principal y Gemini CLI queda como destino heredado solo de pago.

---

## 1. Contexto: la Transición de Gemini CLI a Antigravity CLI

```text
 2025-11  Antigravity (IDE agéntico, fork de VS Code) se lanza
 2026-01  Gemini CLI v0.26.0: hooks activos por defecto
 2026-05-19  Gemini CLI se integra en Antigravity
             -> Antigravity CLI (`agy`, Go) + Antigravity 2.0 (desktop) comparten harness
 2026-06-18  Gemini CLI deja de servir a usuarios gratuitos y a Google AI Pro/Ultra
             -> sigue con API keys de pago y licencias Enterprise Code Assist
```

Fuentes: [anuncio de transición](https://developers.googleblog.com/en/an-important-update-transitioning-gemini-cli-to-antigravity-cli/), [guía de migración](https://www.antigravity.google/docs/cli/gcli-migration). La documentación de geminicli.com muestra el banner "replaced by Antigravity CLI".

---

## 2. Antigravity

### 2.1 Hooks

Fuente: [docs/hooks](https://www.antigravity.google/docs/hooks/) ([texto plano](https://antigravity.google/docs/hooks.md)).

**Eventos:** `SessionStart`, `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop`.

**Ubicaciones:**

| Nivel | Ruta |
| :--- | :--- |
| Workspace | `.agents/hooks.json` |
| Global | `~/.gemini/config/hooks.json` |
| Plugin | `hooks.json` en la **raíz** del plugin (no `hooks/hooks.json`) |
| ⚠️ Ignorado | La clave `hooks` en `~/.gemini/antigravity-cli/settings.json` existe pero **nunca se carga** |

**Formato:** el objeto raíz se indexa por **nombre de hook**, no por evento. `timeout` en **segundos** (30 por defecto). Solo existe `type: "command"`. Los eventos sin tool (`SessionStart`, `PreInvocation`, `PostInvocation`, `Stop`) reciben una lista plana sin matcher.

```json
{
  "synapse-guard": {
    "enabled": true,
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [{ "type": "command", "command": "python3 ./hooks/block_dangerous.py", "timeout": 30 }]
      }
    ]
  }
}
```

**Matcher:** regex sobre el nombre del tool (`""`, `"*"`, `"run_command|view_file"`, `"browser_.*"`).

**Entrada (`stdin`): camelCase, sin parecido con Claude.**

| Campo | Nota |
| :--- | :--- |
| `conversationId` | Equivale a `session_id` |
| `workspacePaths[]` | **Reemplaza a `cwd` y a `CLAUDE_PROJECT_DIR`** |
| `transcriptPath`, `artifactDirectoryPath`, `modelName`, `lastUserInput` | |
| `toolCall: {name, args}` | Solo en `PreToolUse` |
| `stepIdx` | Solo en `PreToolUse` |

No hay `cwd`, `session_id`, `tool_name` ni `tool_input`.

**Salida (`stdout`) en `PreToolUse`:**

```json
{ "decision": "deny", "reason": "...", "overwrite": { }, "permissionOverrides": ["command(npm test)"] }
```

| `decision` | Significado |
| :--- | :--- |
| `allow` | Pasa al siguiente hook o a la política por defecto |
| `auto_approve` | Aprueba cuando la política iba a preguntar |
| `deny` | Bloqueo duro |
| `ask` | Pregunta, respetando las concesiones "Always Allow" |
| `force_ask` | Pregunta siempre, ignorando concesiones previas |
| `deny_unless_prior_grant` | Bloquea salvo concesión previa |

- `overwrite`: merge superficial sobre los argumentos del tool; el resultado lleva un aviso con las claves cambiadas.
- Contexto al modelo: solo `injectSteps` (`systemMessage`, `userMessage`, `ephemeralMessage`, `errorMessage`) en `SessionStart`, `PreInvocation` y `PostInvocation`. **No en `PreToolUse`**: el único canal de texto es `reason`.
- `PostToolUse` solo devuelve `{}`.

> [!CAUTION]
> **No existe bloqueo por `exit 2`.** La página [build-plugins](https://www.antigravity.google/docs/build-plugins/) indica que los hooks deben salir con 0 e imprimir JSON. **Un código distinto de 0 se registra y se ignora (falla abierto).** El `emit_block` actual de Synapse **no bloquea nada en Antigravity**. Además, cualquier excepción no capturada que termine el proceso con código ≠ 0 también deja pasar el tool.

**Proceso del hook:** la página no documenta variables de entorno ni el directorio de trabajo. Los hooks de plugin corren con el **cwd = raíz del plugin** (se recomiendan rutas relativas: `node ./scripts/x.mjs`). `${PLUGIN_ROOT}` y `${PLUGIN_DATA}` se expanden **solo en la configuración MCP**, no en los comandos de hooks.

**Estado:** la documentación no los marca como preview y dice que funcionan en App, CLI e IDE. Sin embargo, un [reporte de la comunidad](https://discuss.ai.google.dev/t/do-antigravity-ide-2-0-actually-execute-plugin-hooks-pretooluse-posttooluse-or-is-that-cli-only-right-now/176814) (agosto 2026: IDE 2.1.1, desktop 2.5.0, CLI 1.1.10) indica que **solo se disparan en `agy` CLI**, sin respuesta de Google.

### 2.2 Tools y Argumentos (PascalCase)

| Propósito | Tool | Argumentos |
| :--- | :--- | :--- |
| Shell | `run_command` | `CommandLine`, `Cwd`, `WaitMsBeforeAsync`, `RunPersistent`, `RequestedTerminalID` |
| Lectura | `view_file` | `AbsolutePath`, `StartLine`, `EndLine`, `IsSkillFile` |
| Escritura | `write_to_file` | `TargetFile`, `Overwrite`, `CodeContent`, `Description`, `IsArtifact`, `ArtifactMetadata` |
| Edición | `replace_file_content` | `TargetFile`, `TargetContent`, `ReplacementContent`, `AllowMultiple`, `StartLine`, `EndLine`, `Instruction`, `Description` |
| Multi-edición | `multi_replace_file_content` | `TargetFile`, `ReplacementChunks[]` (campos internos **no documentados**) |

Otros tools que pueden exponer contenido de archivos: `grep_search` (`SearchPath`, `Query`), `find_by_name`, `list_dir`, `read_url_content`, `search_web`, `browser_*`, subagentes, `ask_permission`, `list_permissions`.

### 2.3 Guardrails Nativos: Permisos, Sandbox y Strict Mode

Fuentes: [permissions](https://www.antigravity.google/docs/permissions/), [sandbox](https://www.antigravity.google/docs/sandbox/), [settings](https://antigravity.google/docs/settings?tab=ide).

**Reglas de permisos** con formato `acción(objetivo)`:

- Acciones: `read_file`, `write_file`, `read_url`, `execute_url`, `download_url`, `command` (prefijo, `regex:` o `*`), `unsandboxed`, `mcp`.
- Listas `deny`, `ask`, `allow` con precedencia **Deny > Ask > Allow**.
- Sustitución de comandos, redirección o expansión de llaves obligan a coincidencia exacta de la línea completa.
- Los envoltorios `timeout`, `env`, `nohup`, `nice` se desenvuelven antes de evaluar.
- Implicaciones: permitir escritura implica lectura; negar lectura implica negar escritura.

**Configuración del CLI** (`~/.gemini/antigravity-cli/settings.json`): `permissions.{allow,ask,deny}`, `agentMode` (`auto`/`default`/`accept-edits`/`plan`), `toolPermission` (`request-review`/`proceed-in-sandbox`/`strict`/`always-proceed`), `artifactReviewPolicy`, `enableTerminalSandbox`, `allowNonWorkspaceAccess`.

**Presets de la App:** Request Review, Default (sandbox activo, sin red), Auto, Turbo.

**Strict mode (IDE):** fuerza revisión en terminal, JavaScript del navegador y artefactos; ignora la allowlist de terminal; respeta `.gitignore`; limita el acceso al workspace; activa el sandbox sin red. "Secure mode" parece ser un nombre anterior (aparece en un codelab, sin confirmar).

**Sandbox:** namespaces en Linux, Seatbelt en macOS, AppContainer en Windows 11 24H2+. **Bloquea `~/.ssh`, `.env` y escrituras fuera del workspace.** Está apagado por defecto en el CLI. Hay [reportes de bugs](https://discuss.ai.google.dev/t/bug-allow-list-terminal-commands-entries-are-moved-to-terminal-commands-after-ide-restart/178583) en la allowlist.

### 2.4 Plugins y Empaquetado

Fuentes: [build-plugins](https://www.antigravity.google/docs/build-plugins/), [plugins](https://www.antigravity.google/docs/plugins/), [rules](https://www.antigravity.google/docs/rules/).

```text
synapse/
├── .antigravity-plugin/
│   └── plugin.json      <-- precede a un plugin.json en la raíz
├── hooks.json           <-- en la RAÍZ, indexado por nombre de hook
├── skills/<name>/SKILL.md  <-- `name` del frontmatter = nombre de la carpeta
├── agents/
├── rules/
│   ├── AGENTS.md        <-- siempre activo
│   └── <x>.md           <-- frontmatter `trigger: always_on|model_decision|glob|manual`
└── mcp_config.json
```

- Manifiesto: requiere `name` (kebab-case), `displayName`, `description`, `version`, `logo`, `author`. **`additionalProperties: false`**: claves desconocidas fallan la validación.
- **Una regla sin `trigger` válido se descarta en silencio.**
- No existe `commands/`: las skills lo reemplazan.
- Comandos: `agy plugin install <dir|github-url|name@antigravity-plugins-official>`, `agy plugin validate --strict`, `agy plugin list`, `agy plugin enable`.
- Ubicaciones: `.agents/plugins/` (workspace), `~/.gemini/config/plugins/` (global).
- **`agy plugin import claude`** importa desde `~/.claude/plugins/`; `agy plugin import gemini` importa extensiones de Gemini. **No está documentado cómo convierte los hooks**: hay que probarlo con Synapse.
- Reglas de contexto: `AGENTS.md`, `GEMINI.md`, `.agents/rules/*.md`, legado `.agent/rules/`. Límite de 24 KB por archivo y 20k tokens compartidos. `CLAUDE.md` no se menciona.
- MCP: `.agents/mcp_config.json`, `~/.gemini/config/mcp_config.json`.

---

## 3. Gemini CLI (heredado)

### 3.1 Hooks

Fuentes: [hooks](https://geminicli.com/docs/hooks/), [referencia](https://geminicli.com/docs/hooks/reference/), [blog](https://developers.googleblog.com/en/tailor-gemini-cli-to-your-workflow-with-hooks/).

- Activos por defecto desde **v0.26.0** (28 de enero de 2026).
- **Eventos:** `BeforeTool`, `AfterTool`, `BeforeAgent`, `AfterAgent`, `BeforeModel`, `AfterModel`, `BeforeToolSelection`, `SessionStart`, `SessionEnd`, `PreCompress`, `Notification`.
- **Configuración:** clave `hooks` en `settings.json` con precedencia `.gemini/settings.json` > `~/.gemini/settings.json` > `/etc/gemini-cli/settings.json` > extensiones. `timeout` en **milisegundos** (60000 por defecto). Campos `matcher` (regex) y `sequential`. Los hooks de proyecto se identifican por huella: si cambian, se avisa antes de ejecutarlos.

**Entrada: cercana a Claude.** `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `timestamp`; `BeforeTool` agrega `tool_name`, `tool_input`, `mcp_context`, `original_request_name`.

**Salida:** `decision` (`allow` | `deny`/`block`), `reason`, `systemMessage`, `continue`, `stopReason`, `suppressOutput`; `hookSpecificOutput.tool_input` reescribe argumentos.

**Códigos de salida** ([hookRunner.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/hooks/hookRunner.ts)):

| Código | Efecto |
| :--- | :--- |
| 0 | stdout se parsea como JSON; texto no JSON → allow + `systemMessage` |
| 1 | allow con advertencia |
| **2 u otro** | **deny, con stderr como motivo** → el `emit_block` de Synapse funciona sin cambios |
| Salida no parseable | allow |
| Timeout | SIGTERM |

**`ask`:** el tipo `HookDecision` incluye `'ask'` ([types.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/hooks/types.ts)), pero [coreToolHookTriggers.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/core/coreToolHookTriggers.ts) solo maneja stop, block y reescritura. **`ask` efectivamente no está soportado**: el `permissionDecision:"ask"` de Synapse se ignora y equivale a **allow**.

**`additionalContext`:** se procesa en `AfterTool`, `BeforeAgent` y `SessionStart`; **se ignora en `BeforeTool`**.

**Entorno del hook:** corre en el `cwd` del payload, con la shell del usuario y entorno saneado. Variables: `GEMINI_PROJECT_DIR`, `GEMINI_CWD`, `GEMINI_SESSION_ID`, `GEMINI_PLANS_DIR` y **`CLAUDE_PROJECT_DIR` como alias**. No se definen `CLAUDE_PLUGIN_ROOT` ni `CLAUDE_CONFIG_DIR`.

### 3.2 Tools

| Tool | Argumentos |
| :--- | :--- |
| `run_shell_command` | `command`, `description`, `dir_path`, `is_background` |
| `read_file` | `file_path`, `start_line`, `end_line` |
| `read_many_files` | `include`, `exclude`, `recursive`, ... |
| `write_file` | `file_path`, `content` |
| `replace` | `file_path`, `old_string`, `new_string`, `instruction`, `allow_multiple` |
| `grep_search` (alias `search_file_content`) | `pattern`, `dir_path`, ... |

También `glob`, `list_directory`, `web_fetch`; MCP como `mcp_<server>_<tool>`. Fuente: [tools](https://geminicli.com/docs/reference/tools/).

### 3.3 Extensiones

Fuente: [extensions reference](https://geminicli.com/docs/extensions/reference/).

- Manifiesto `gemini-extension.json`: `name`, `version`, `description`, `mcpServers`, `contextFileName` (`GEMINI.md` por defecto), `excludeTools`, `settings`, `plan`, `themes`, `migratedTo`.
- Layout: **`hooks/hooks.json` (misma ruta que Synapse)**, `commands/*.toml`, `skills/<name>/SKILL.md`, `policies/*.toml` (las decisiones `allow` de extensiones se ignoran), `agents/*.md`.
- Variables en `hooks.json`: `${extensionPath}`, `${workspacePath}`, `${/}`.
- `gemini extensions install|link|new`; galería en geminicli.com/extensions.
- No se confirmó si el `hooks.json` de una extensión va envuelto en `{"hooks": {...}}`.

**Policy engine** ([referencia](https://geminicli.com/docs/reference/policy-engine/)): reglas TOML `[[rule]]` con `toolName`, `commandPrefix`, `commandRegex`, `argsPattern` (regex sobre los argumentos serializados), `decision` = `allow|deny|ask_user`, `priority`, `denyMessage`, `modes`, `allowRedirection`. `ask_user` actúa como `deny` en modo headless. El nivel de política de workspace no funciona actualmente.

### 3.4 Herramienta de Migración

**`gemini hooks migrate --from-claude`** ([migrate.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/cli/src/commands/hooks/migrate.ts)):

- Lee `.claude/settings.local.json` o `.claude/settings.json`. **No lee el `hooks.json` de plugins.**
- Escribe `.gemini/settings.json` (workspace), reemplazando las claves de evento existentes.

| Claude | Gemini |
| :--- | :--- |
| `PreToolUse` | `BeforeTool` |
| `PostToolUse` | `AfterTool` |
| `UserPromptSubmit` | `BeforeAgent` |
| `Stop` / `SubagentStop` | `AfterAgent` |
| `PreCompact` | `PreCompress` |
| `Bash` / `Edit` / `Write` / `Read` | `run_shell_command` / `replace` / `write_file` / `read_file` |

Convierte timeouts a milisegundos y `$CLAUDE_PROJECT_DIR` a `$GEMINI_PROJECT_DIR`. **No traduce los esquemas de stdin/stdout**, no mapea `${CLAUDE_PLUGIN_ROOT}` y escribe `grep`/`ls` cuando los nombres reales son `grep_search`/`list_directory`.

---

## 4. Brechas por Hook

| Hook | Antigravity | Gemini CLI |
| :--- | :--- | :--- |
| `block_dangerous` | Leer `toolCall.args.CommandLine` y `Cwd`. **Todo bloqueo debe salir como `exit 0` + `{"decision":"deny"}`.** `ask` → `ask` o `force_ask` (`.git`, force push). Sin `additionalContext`: incorporar `git_recoverable_delete` al `reason`. Raíces desde `workspacePaths[]`. | `tool_input.command` + `dir_path`. `exit 2` funciona. **Se pierde `ask`** (Laya y deterministas): degradar a `deny` con motivo, más seguro que `allow`. Se pierde `additionalContext`. |
| `block_env_read` | `view_file.AbsolutePath`; considerar `grep_search`, `find_by_name`. El sandbox ya bloquea `.env` para shell si está activo. Esquema ofuscado en `reason`. | `read_file.file_path` + `read_many_files.include[]` (resolver globs) + `grep_search`. stderr funciona como motivo. |
| `detect_secrets` | `write_to_file.CodeContent`, `replace_file_content.ReplacementContent`, `multi_replace_file_content.ReplacementChunks[*]` (verificar campos). | `write_file.content`, `replace.new_string`. |
| `protect_files` | `TargetFile`. Agregar `.agents/hooks.json`, `.agents/mcp_config.json`, `~/.gemini/antigravity-cli/settings.json`. | `file_path`. Agregar `.gemini/settings.json`, `.gemini/policies/`. |

---

## 5. Tablas de Compatibilidad

### 5.1 Antigravity

| Funcionalidad | ¿Soportada? | Notas | Fuente |
| :--- | :--- | :--- | :--- |
| Hook previo | Sí (`PreToolUse`) | Confirmado en CLI; IDE/App reportados sin disparar | hooks; foro |
| Bloquear | Sí, solo JSON `deny` | **`exit 2` se ignora** | build-plugins |
| Preguntar | Sí (`ask`, `force_ask`) | Más rico que Claude | hooks |
| `allow` explícito | Sí (`allow`, `auto_approve`) | `allow` solo pasa al siguiente | hooks |
| Reescribir input | Sí (`overwrite`) | | hooks |
| `additionalContext` en `PreToolUse` | **No** | Solo `reason` | hooks |
| stdin con forma de Claude | **No** | camelCase + PascalCase | hooks |
| Variables de entorno | **No documentadas** | cwd = raíz del plugin | build-plugins |
| Plugin con hooks, skills y reglas | Sí | Manifiesto y rutas distintos | build-plugins |
| Import de plugins Claude | Sí | Fidelidad no documentada | plugins |
| Protección nativa de `.env` / workspace | Sí | Sandbox apagado por defecto en CLI | sandbox |

### 5.2 Gemini CLI

| Funcionalidad | ¿Soportada? | Notas | Fuente |
| :--- | :--- | :--- | :--- |
| Hook previo | Sí (`BeforeTool`) | v0.26.0+ | blog |
| Bloquear con `exit 2` | Sí | Sin cambios | hookRunner.ts |
| Bloquear con JSON | Sí | `decision: "deny"` | referencia |
| Preguntar | **No** | `ask` → allow | types.ts; coreToolHookTriggers.ts |
| `additionalContext` en `BeforeTool` | **No** | | referencia |
| Reescribir input | Sí | `hookSpecificOutput.tool_input` | referencia |
| stdin | Casi compatible | Nombres de tool/args distintos | referencia |
| `CLAUDE_PROJECT_DIR` | Sí (alias) | | hooks |
| `CLAUDE_PLUGIN_ROOT` | No | `${extensionPath}` | extensions |
| Extensión con hooks, skills, contexto | Sí | | extensions |
| Herramienta de migración | Parcial | Solo configuración | migrate.ts |

---

## 6. Pendiente de Verificar

- Si los hooks se disparan en el IDE y la App de Antigravity.
- Variables de entorno, semántica de timeout y directorio de trabajo de los hooks de Antigravity.
- Campos de `ReplacementChunks`.
- Qué hace `agy plugin import claude` con Synapse tal cual.
- Si Gemini CLI actúa sobre `decision:"ask"` en runtime.
- Forma exacta del `hooks/hooks.json` de una extensión de Gemini.
- Licencia del repo `google-antigravity/antigravity-cli`.

> [!NOTE]
> Una [guía de Medium](https://medium.com/google-cloud/a-developers-guide-to-agent-hooks-in-antigravity-cli-4c1440febd11) cita `~/.gemini/antigravity-cli/hooks.json` y "cinco hooks principales", y un post de DEV muestra `.agents/config.json` con `before_tool_call`. Ambos contradicen la documentación oficial y probablemente están desactualizados.
