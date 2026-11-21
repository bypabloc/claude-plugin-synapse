# Claude Code Plugin: Synapse (`synapse`)

> [!NOTE]
> Este repo (`bypabloc/agent-plugin-synapse`) es un **espejo** generado desde el monorepo [`bypabloc/agent-plugins`](https://github.com/bypabloc/agent-plugins/tree/main/plugins/synapse). Los issues y pull requests van allí. El soporte para Codex, Antigravity y OpenCode se diseñó en el [spec 001](https://github.com/bypabloc/agent-plugins/tree/synapse-v1.7.0/docs/specs/001-plugins-multi-agente).

[![Claude Code Compatible](https://img.shields.io/badge/Claude%20Code-Plugin-blueviolet)](https://github.com/anthropics/claude-plugins-official)
[![System 1 AI: Laya Powered](https://img.shields.io/badge/Decision%20Engine-Laya%20System%201-orange)](https://github.com/convaiinnovations/laya)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Marketplace Ready](https://img.shields.io/badge/Marketplace-Verified-success)](https://github.com/bypabloc/claude-plugins)
[![Tests: 100%](https://img.shields.io/badge/Tests-Passing-brightgreen)](tests/)

> **Plugin oficial de seguridad perimetral para Claude Code** que intercepta las operaciones de herramientas en tiempo de ejecución (`PreToolUse` para `Bash`, `Read`, `Edit` y `Write`). Combina firmas heurísticas determinísticas de ultra-alta velocidad (1ms) con inferencia clasificatoria neuronal **System 1 (Laya)** acelerada por GPU/CUDA (~25ms), garantizando **cero consumo de contexto (Zero Context Cost)** y fallback automático transparente sin dependencias forzadas.

---

## 1. ¿Para qué sirve y cuál es el objetivo?

Durante sesiones de desarrollo autónomas o interactivas con Claude Code, los agentes ejecutan comandos de terminal y modifican código a gran velocidad. El objetivo de **Synapse** es actuar como un **firewall perimetral determinístico y semántico**:

1. **Prevención de Catástrofes en Terminal (`Bash`):** Bloquea de inmediato comandos destructivos irreparables (`rm -rf /`, `rm -rf ~`, manipulación directa de `/dev/sd*`, fork bombs `:(){ :|:& };:`, `chmod 777 /`, `git push --force`).
2. **Autorización Incondicional de Temporales y Artefactos:** Elimina la fricción de aprobaciones manuales para carpetas temporales (`./tmp/**`, `/tmp/claude-*`) y artefactos de compilación (`dist/`, `build/`, `.turbo/`, `target/`, `.cache/`, `node_modules/`, etc.).
3. **Protección contra Lectura y Fuga de Secretos (`.env*`):** Impide que Claude lea o vuelque en el contexto el contenido en texto plano de archivos `.env`. En su lugar, analiza e infiere un esquema seguro con tipos y formatos ofuscados (ejemplo: `DATABASE_URL=<URL, 35 caracteres>`, `API_KEY=<alfanumerico, 32 caracteres>`).
4. **Detección Preventiva de Credenciales (`Edit` / `Write`):** Detecta inyecciones de claves privadas (RSA/SSH/PGP), tokens de proveedores cloud (OpenAI, Anthropic, Google, AWS, Stripe, GitHub, Slack) y contraseñas hardcodeadas antes de que se escriban en disco.
5. **Gobernanza de Infraestructura del Repositorio:** Protege de sobreescritura accidental archivos troncales (`.git/`, lockfiles `package-lock.json`, `pnpm-lock.yaml`, `bun.lock`, `uv.lock`, `poetry.lock`, `Cargo.lock`) y solicita confirmación interactiva para configuraciones de agentes (`.claude/settings.json`, `.claude/hooks/**`).
6. **Zero Context Cost:** Opera 100% en el harness nativo de Claude Code. No inyecta tokens en la conversación ni compite por la ventana de contexto del LLM.

---

## 2. Arquitectura de Inferencia y Fallback Automático

Synapse implementa una estrategia de **Defensa en Profundidad** de doble vía:

```
[Claude Code Tool Call] ──► (PreToolUse Event via stdin)
                                   │
                                   ▼
                   ┌───────────────────────────────┐
                   │    Filtro Heurístico Base     │
                   │ (Firmas regex, rutas y hashes) │
                   └───────────────┬───────────────┘
                                   │
         ┌─────────────────────────┴─────────────────────────┐
         │ ¿Coincidencia estática concluyente?              │
        SÍ                                                   NO
         │                                                   │
         ▼                                                   ▼
┌──────────────────┐                               ┌──────────────────┐
│ Decisión Inmediata│                               │ ¿GPU / Laya OK?  │
│ (ALLOW / BLOCK / │                               └────────┬─────────┘
│  ASK en ~1ms)    │                                        │
└──────────────────┘                      ┌─────────────────┴─────────────────┐
                                         SÍ                                   NO
                                          │                                   │
                                          ▼                                   ▼
                               ┌──────────────────────┐             ┌──────────────────┐
                               │ Laya System 1 Router │             │ Modo FALLBACK    │
                               │ Clasificación neural │             │ Reglas estándar  │
                               │ forward-pass (~25ms) │             │ deterministas    │
                               └──────────────────────┘             └──────────────────┘
```

- **Laya System 1:** Modelo liviano de clasificación rápida de intenciones basado en representaciones neuronales densas. Evalúa consultas de sensibilidad semántica sin recurrir a llamadas costosas a modelos generativos de lenguaje.
- **Hardware Agnostic:** Si CUDA/GPU está disponible, precarga el modelo en VRAM. Si se ejecuta en entornos sin acelerador o máquinas de integración continua, conmuta automáticamente a modo **FALLBACK** determinístico sin interrumpir el flujo del agente.

---

## 3. Inventario de Hooks y Protocolo PreToolUse

Todos los scripts residen en `hooks/` y se rigen por la especificación oficial de Claude Code Plugins:

| Script | Evento | Herramientas | Política y Comportamiento |
| :--- | :--- | :--- | :--- |
| [`block_dangerous.py`](hooks/block_dangerous.py) | `PreToolUse` | `Bash` | **Auto-Allow:** Operaciones sobre `./tmp/`, `/tmp/claude-*`, `dist/`, `.cache/`.<br>**Block (exit 2):** `rm -rf /`, `git push --force`, fork bombs, `/dev/sd*`, escrituras fuera del entorno git del proyecto (con variables resueltas: `p=/home/x; rm -rf "$p"`), y escribir archivos desde Bash (`echo >`, `cat <<EOF >`, `sed -i`, scripts inline que escriben): usar `Write`/`Edit`.<br>**Ask (exit 0):** Cualquier comando que modifique la carpeta `.git` (leerla no), salida redirigida a `/tmp` del sistema, eliminación permanente de código fuente o archivos base, y, con Laya encendida, comandos que Laya afinado marca como riesgosos (ver `models/laya-block-dangerous.json`).<br>**Pass sin Laya:** lecturas, `git`/`gh` rutinarios, `bun|npm|pnpm run|test`, `uv run <herramienta>`, scripts versionados del proyecto y los prefijos de `.claude/synapse-commands.json`. |
| [`block_env_read.py`](hooks/block_env_read.py) | `PreToolUse` | `Read`, `Bash` | **Allow:** Plantillas `.env.example`, `.env.sample`, comandos `source .env`.<br>**Block (exit 2):** Lectura con `Read` o comandos `cat`, `head`, `tail`, `awk`, `strings`, `python`, `node`. Devuelve esquema ofuscado. |
| [`detect_secrets.py`](hooks/detect_secrets.py) | `PreToolUse` | `Edit`, `Write` | **Allow:** Placeholders (`your-api-key`, `REPLACE_ME`, `process.env`, `os.getenv`).<br>**Block (exit 2):** Llaves privadas RSA/SSH, OpenAI, Anthropic, Google, AWS, Stripe, GitHub, contraseñas en texto plano y tokens detectados por Laya.<br>**Excepción por proyecto:** `.claude/synapse-secrets.json` (en `$CLAUDE_PROJECT_DIR`, o el `cwd` del payload) con `{"password_rule_values": ["required"]}` deja pasar la firma de contraseña solo si el valor capturado coincide exactamente (sensible a mayúsculas) con un elemento. Cualquier literal igual pasa en ese proyecto, aunque fuera una credencial real. Archivo ausente o inválido: bloquea. |
| [`protect_files.py`](hooks/protect_files.py) | `PreToolUse` | `Edit`, `Write` | **Allow:** Código fuente general, plantillas `.dist`/`.template`.<br>**Block (exit 2):** Modificación manual de `.git/`, `.venv/` o lockfiles de dependencias.<br>**Ask (exit 0):** Ajustes de configuración de agentes (`.claude/settings.json`) y de Synapse (`.claude/synapse-*.json`). |

---

## 4. Guía de Instalación Rápida (2 Comandos)

Cualquier usuario puede instalar y activar el plugin en Claude Code ejecutando estos dos comandos en su terminal:

```bash
# 1. Registrar el marketplace público de bypabloc (solo una vez)
claude plugin marketplace add bypabloc/claude-plugins

# 2. Instalar y habilitar el plugin
claude plugin install synapse@bypabloc
```

> [!NOTE]
> Si en tu terminal no tienes configuradas llaves SSH para GitHub, puedes utilizar alternativamente la URL HTTPS pública en el primer comando:
> ```bash
> claude plugin marketplace add https://github.com/bypabloc/claude-plugins
> claude plugin install synapse@bypabloc
> ```

### Comprobar la instalación:
Para verificar que el plugin está activo y ver el registro formal de hooks `PreToolUse`:

```bash
claude plugin list
claude plugin details synapse@bypabloc
```

### En Codex, Antigravity y OpenCode

Synapse es multi-agente desde 1.7.0 ([spec 001](https://github.com/bypabloc/agent-plugins/tree/synapse-v1.7.0/docs/specs/001-plugins-multi-agente)). La lógica es la misma en todos; cambian la entrada y la salida de cada agente (`hooks/hosts.py`). Requisito: Python 3.10 o superior (`python3` en Linux y macOS; `py -3` o `python` en Windows, o `SYNAPSE_PYTHON`).

| Agente | Instalación (WSL, Linux, macOS o Windows) | Cómo decide |
| :--- | :--- | :--- |
| **Codex** ≥ 0.171 | `codex plugin marketplace add bypabloc/agent-plugins` · `codex plugin add synapse@bypabloc-agents` · luego `/hooks` para confiar en los hooks (Codex los ata al hash: repítelo tras cada actualización) | Bloquea con `deny`. Codex no puede preguntar desde un hook: un `ask` responde `deny` con un id, y **tú** apruebas una sola ejecución en tu terminal con `python3 <plugin>/scripts/synapse.py approve <id>` (el agente no puede hacerlo) |
| **Antigravity** (`agy` ≥ 1.5.4) | `agy plugin install https://github.com/bypabloc/agent-plugin-synapse` | `deny` y `force_ask` nativos. Un error interno pide confirmación (en Antigravity un error equivale a permitir) |
| **OpenCode** V2 ≥ 2.3 | En `opencode.json`: `"plugins": ["@bypabloc/synapse@git+https://github.com/bypabloc/agent-plugin-synapse.git"]` (opción `{"python": "…"}` para fijar el intérprete) | `throw` para bloquear y `permission.assert` para preguntar. Si Synapse no responde, pregunta |

- **Diagnóstico:** `python3 <plugin>/scripts/synapse.py doctor` revisa el intérprete, los manifiestos, la versión de cada CLI, el delta de Laya y los logs.
- **Trazas:** Claude Code escribe en `${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse/`; los demás agentes en `~/.local/state/synapse/logs/` (`%LOCALAPPDATA%\synapse\logs` en Windows). `scripts/synapse.py log --host codex` las muestra.
- **Config por proyecto:** `.synapse/commands.json` y `.synapse/secrets.json`, con respaldo en los archivos `.claude/synapse-*.json`.
- **Límites:**
  - En Windows nativo, Synapse ya se importa y protege rutas y escrituras (incluido `Set-Content` de Codex), pero el análisis de comandos de PowerShell es parcial: el mínimo está en `windows.*` y la cobertura completa es el spec 002.
  - Laya no corre en Windows nativo (necesita sockets Unix).
  - En Codex no existe una herramienta de lectura: el bloqueo de `.env` se aplica a los comandos de shell.

---

### Método B: Probar en Modo Desarrollo (Sin Instalar)

Si deseas probar o depurar el plugin directamente desde el repositorio clonado:

```bash
git clone https://github.com/bypabloc/agent-plugin-synapse.git
claude --plugin-dir "./agent-plugin-synapse"
```

---

## 5. Auditoría de Logs y Variables de Entorno

Cada ejecución de un hook escribe **un paso por línea** (JSONL) en `${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse/AAAA-MM-DD.jsonl`, agrupado por `run` y con el `session` de Claude Code. El contenido de `Write`/`Edit` nunca se registra y los tokens se enmascaran.

```bash
python3 scripts/synapse_log.py --since 30m          # últimos 30 minutos (30m, 2h, 1d)
python3 scripts/synapse_log.py --decision block     # allow | ask | block | pass
python3 scripts/synapse_log.py --session 49c43f62 --hook block_dangerous
python3 scripts/synapse_log.py --json | jq .        # JSONL crudo
python3 scripts/synapse_log.py --follow             # en vivo
```

```
━━ 2026-10-02 09:00:56.720 ━━ block_dangerous ━━ run 29e84acc ━━ session 49c43f62
   tool    Bash
   cwd     /home/bypabloc/projects/bypabloc/claude-plugin-synapse
   engine  laya-daemon-gpu
   input   p=/home/bypabloc; rm -rf "$p/otro"
   ├─ regex.catastrophic       result=no_match  checked=21
   ├─ structure.outside_project  result=match  subcmd=rm -rf "$p/otro"  target=/home/bypabloc/otro  roots=[...]
   └─ DECISION  BLOCK  python · structure.outside_project  (7.6 ms)  Prohibido modificar rutas fuera del proyecto (...): 'rm' sobre /home/bypabloc/otro.
      evidencia  {"subcmd": "rm -rf \"$p/otro\"", "target": "/home/bypabloc/otro", "roots": ["..."]}
```

### Configuración de Variables de Entorno:

| Variable | Descripción | Valor por Defecto |
| :--- | :--- | :--- |
| `SYNAPSE_LAYA` | Switch global de Laya. `on` la enciende en todos los hooks (o solo en los de `SYNAPSE_LAYA_HOOKS`); `off` la apaga en todos y pesa sobre cualquier otra variable. Sin definir, Laya corre solo en los hooks de `SYNAPSE_LAYA_HOOKS`. | Sin definir: **Laya apagada**, solo reglas deterministas |
| `SYNAPSE_LAYA_HOOKS` | Hooks donde corre Laya, separados por comas (`block_dangerous,detect_secrets,protect_files,block_env_read`). | Vacía |
| `SYNAPSE_LOG_DIR` | Reemplaza el directorio de la traza JSONL (`AAAA-MM-DD.jsonl`). Ideal para testing. | `${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse/` |
| `SYNAPSE_DEBUG` | Activa mensajes informativos de diagnóstico y aceleración de hardware en `stderr` (`1` para activar). | Desactivado (`0`) para evitar ruido en terminal |
| `SYNAPSE_LOG_RETENTION_DAYS` | Días que se conservan las trazas diarias; las más antiguas se borran al crear la del día (`0` = conservar todo). | `30` |
| `SYNAPSE_LAYA_IDLE_SECONDS` | Segundos sin uso tras los que el daemon de Laya termina y libera la VRAM. | `1200` |
| `SYNAPSE_LAYA_STARTUP_SECONDS` | Espera máxima del hook a que el daemon arranque; si se agota, ese hook sigue solo con reglas deterministas. | `40` |
| `SYNAPSE_RUNTIME_DIR` | Carpeta del socket y del log del daemon. | `$XDG_RUNTIME_DIR` o `~/.cache` (+ `/synapse`) |

### Encender o apagar Laya

Laya viene **apagada por defecto**: en la traza real de un proyecto (oct-nov 2026) se consultó ~20 mil veces en
`block_dangerous`, costó ~205 ms por comando (contra ~8 ms del código) y sus 24 confirmaciones fueron falsos
positivos; todo lo peligroso lo detuvieron las reglas deterministas. Se enciende desde el `env` de
`settings.json`, global (`~/.claude/settings.json`) o por proyecto (`.claude/settings.json`):

```jsonc
{ "env": { "SYNAPSE_LAYA": "on" } }                              // todos los hooks
{ "env": { "SYNAPSE_LAYA_HOOKS": "block_dangerous" } }           // solo ese hook
{ "env": { "SYNAPSE_LAYA": "off" } }                             // apagada aunque haya lista
```

### Comandos rutinarios por proyecto (`.claude/synapse-commands.json`)

Los sub-comandos que empiezan con alguno de estos prefijos se tratan como desarrollo rutinario: Laya no los
evalúa (decisión `pass`; Claude Code aplica sus permisos normales). Las reglas de bloqueo y confirmación
siguen aplicando antes. Editar el archivo pide confirmación (`protect_files`).

```json
{ "routine_commands": ["bun devtools/run.ts", "graphify"] }
```

Para proponer la lista a partir de la traza de un proyecto:

```bash
python3 scripts/learn_from_log.py --project ~/projects/x --replay   # resumen, replay y sugerencias
```

---

## 6. Verificación de Calidad y Pruebas

Las pruebas usan `pytest` (`uv pip install -e ".[dev]"`) y se organizan así:

```
tests/
├── conftest.py              # Opción --laya, logs aislados por test, limpieza de repos efímeros
├── support.py               # Ejecución de hooks (in-process / shell), carga de casos, repos git efímeros
├── cases/                   # Casos declarativos por hook (payload → exit / decisión / stderr)
│   ├── block_dangerous.json
│   ├── block_env_read.json
│   ├── detect_secrets.json
│   └── protect_files.json
├── unit/                    # Un módulo por hook + reglas de common.py
│   ├── test_block_dangerous.py    # Casos JSON + coherencia de señales Laya (mock)
│   ├── test_block_env_read.py
│   ├── test_detect_secrets.py
│   ├── test_protect_files.py
│   ├── test_disposable_targets.py # tmp/, scratchpad, build, gitignored, sin seguimiento
│   ├── test_audit_log.py
│   └── test_manifest.py
└── e2e/
    └── test_plugin_hooks.py # Pipeline PreToolUse completo desde hooks.json + traza y vista de logs
```

```bash
claude plugin validate --strict .   # Manifiesto
python3 -m pytest                   # Todo en modo fallback determinístico (~40 s)
python3 -m pytest --laya gpu        # Con Laya System 1 en CUDA (--laya cpu para CPU)
python3 -m pytest tests/e2e         # Solo e2e
```

Para agregar un caso a un hook basta con sumar una entrada en `tests/cases/<hook>.json`. Los fragmentos que GitHub push protection detecta como secretos reales se escriben con marcadores (`{stripe_live_prefix}`) que `support.py` expande.

---

## 7. Licencia

Este proyecto está distribuido bajo la licencia [MIT](LICENSE). Creado y mantenido por [Pablo Contreras (@bypabloc)](https://github.com/bypabloc).
