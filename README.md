# Claude Code Plugin: Synapse (`synapse`)

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
| [`block_dangerous.py`](hooks/block_dangerous.py) | `PreToolUse` | `Bash` | **Auto-Allow:** Operaciones sobre `./tmp/`, `/tmp/claude-*`, `dist/`, `.cache/`.<br>**Block (exit 2):** `rm -rf /`, `git push --force`, fork bombs, `/dev/sd*`.<br>**Ask (exit 0):** Eliminación permanente de código fuente o archivos base. |
| [`block_env_read.py`](hooks/block_env_read.py) | `PreToolUse` | `Read`, `Bash` | **Allow:** Plantillas `.env.example`, `.env.sample`, comandos `source .env`.<br>**Block (exit 2):** Lectura con `Read` o comandos `cat`, `head`, `tail`, `awk`, `strings`, `python`, `node`. Devuelve esquema ofuscado. |
| [`detect_secrets.py`](hooks/detect_secrets.py) | `PreToolUse` | `Edit`, `Write` | **Allow:** Placeholders (`your-api-key`, `REPLACE_ME`, `process.env`, `os.getenv`).<br>**Block (exit 2):** Llaves privadas RSA/SSH, OpenAI, Anthropic, Google, AWS, Stripe, GitHub, contraseñas en texto plano y tokens detectados por Laya. |
| [`protect_files.py`](hooks/protect_files.py) | `PreToolUse` | `Edit`, `Write` | **Allow:** Código fuente general, plantillas `.dist`/`.template`.<br>**Block (exit 2):** Modificación manual de `.git/`, `.venv/` o lockfiles de dependencias.<br>**Ask (exit 0):** Ajustes de configuración de agentes (`.claude/settings.json`). |

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

---

### Método B: Probar en Modo Desarrollo (Sin Instalar)

Si deseas probar o depurar el plugin directamente desde el repositorio clonado:

```bash
git clone https://github.com/bypabloc/claude-plugin-synapse.git
claude --plugin-dir "./claude-plugin-synapse"
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
   engine  laya-cuda
   input   rm -rf .git
   ├─ regex.catastrophic       result=no_match  checked=21
   ├─ structure.git_dir        result=match  subcmd=rm -rf .git  target=.../claude-plugin-synapse/.git
   └─ DECISION  BLOCK  (24.4 ms)  Prohibido modificar .git/ (contiene todo el historial): 'rm' sobre .git.
```

### Configuración de Variables de Entorno:

| Variable | Descripción | Valor por Defecto |
| :--- | :--- | :--- |
| `SYNAPSE_LOG_DIR` | Reemplaza el directorio de la traza JSONL (`AAAA-MM-DD.jsonl`). Ideal para testing. | `${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse/` |
| `SYNAPSE_DEBUG` | Activa mensajes informativos de diagnóstico y aceleración de hardware en `stderr` (`1` para activar). | Desactivado (`0`) para evitar ruido en terminal |
| `LAYA_DEVICE` | Fuerza el acelerador para Laya (`cuda`, `cpu`, `mps`). | Autodetección de hardware (`cuda` si está disponible, sino `cpu`) |

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
