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

Synapse incluye un motor de auditoría unificado y no intrusivo que registra cada decisión (`ALLOW`, `ASK`, `BLOCKED`) con timestamp y justificación técnica:

```log
[2026-10-02 00:19:15] [BLOCKED] [LAYA-CUDA  ] [block_dangerous  ] Tool: Bash  | Target: 'rm -rf /' | Reason: Comando catastrófico bloqueado preventivamente
[2026-10-02 00:19:16] [ALLOW  ] [LAYA-CUDA  ] [block_dangerous  ] Tool: Bash  | Target: 'rm -f ./tmp/cache_item.json' | Reason: Operación en directorio temporal autorizada
```

### Configuración de Variables de Entorno:

| Variable | Descripción | Valor por Defecto |
| :--- | :--- | :--- |
| `SYNAPSE_LOG_DIR` | Define un directorio específico y exclusivo para almacenar `security_hooks.log`. Ideal para entornos de testing y auditorías segregadas. | `~/.claude/logs/` o `CLAUDE_PLUGIN_DATA/logs/` |
| `SYNAPSE_DEBUG` | Activa mensajes informativos de diagnóstico y aceleración de hardware en `stderr` (`1` para activar). | Desactivado (`0`) para evitar ruido en terminal |
| `LAYA_DEVICE` | Fuerza el acelerador para Laya (`cuda`, `cpu`, `mps`). | Autodetección de hardware (`cuda` si está disponible, sino `cpu`) |

---

## 6. Verificación de Calidad y Pruebas

Synapse cuenta con una batería de pruebas automatizadas que validan integridad estructural, logging segregado y ejecución en subprocesos aislados:

```bash
# 1. Validación estructural estricta del manifiesto del plugin
claude plugin validate --strict .

# 2. Pruebas unitarias de integridad y logging en directorio específico
python3 -m unittest discover tests

# 3. Suite completa de guardrails de seguridad (36 casos de prueba en GPU)
python3 tests/test_hooks.py --gpu

# 4. Suite completa de guardrails en modo Fallback determinístico (sin GPU)
python3 tests/test_hooks.py --fallback

# 5. Suite en subprocesos aislados (simula llamadas reales de Claude Code)
python3 tests/test_hooks.py --fallback --subprocess
```

---

## 7. Licencia

Este proyecto está distribuido bajo la licencia [MIT](LICENSE). Creado y mantenido por [Pablo Contreras (@bypabloc)](https://github.com/bypabloc).
