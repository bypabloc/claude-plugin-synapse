# AGENTS.md — Contexto Operativo y Manual de Arquitectura para IA

Este documento define la arquitectura técnica, los protocolos de ciclo de vida de Claude Code, las políticas de seguridad y las pautas de desarrollo para cualquier agente inteligente o desarrollador que mantenga o extienda el plugin **Synapse**.

---

## 1. Visión General del Proyecto

- **Nombre del Plugin:** `synapse`
- **Categoría:** Seguridad, guardrails perimetrales, gobernanza de ejecución.
- **Tipo de Integración:** Hooks de ciclo de vida nativos de Claude Code (`PreToolUse`).
- **Costo de Contexto:** Cero (`Zero Context Cost`). Los hooks operan en el harness de ejecución antes de invocar las herramientas; no consumen tokens en el prompt del LLM.
- **Motor de Decisión Dual:**
  1. **System 1 (Neuronal):** Inferencia ultrarrápida forward-pass con [Laya](https://github.com/convaiinnovations/laya) (~25ms en CUDA/GPU).
  2. **Fallback Determinístico:** Reglas estáticas con expresiones regulares de alta precisión y evaluación estructural de rutas (<1ms en CPU).

---

## 2. Estructura de Directorios

```
claude-plugin-synapse/
├── .claude-plugin/
│   └── plugin.json             # Manifiesto oficial del plugin
├── hooks/
│   ├── hooks.json              # Mapeo PreToolUse -> scripts ejecutables
│   ├── common.py               # Singleton de Laya, detección de GPU, logs y utilidades
│   ├── block_dangerous.py      # Guardrail para herramienta Bash
│   ├── block_env_read.py       # Guardrail para Read y comandos Bash sobre .env
│   ├── detect_secrets.py       # Guardrail para Edit y Write (fuga de credenciales)
│   └── protect_files.py        # Guardrail para Edit y Write (integridad de archivos)
├── rules/
│   └── security-guardrails.md  # Regla descriptiva inyectable para Claude Code
├── skills/
│   └── synapse-status/
│       └── SKILL.md            # Skill slash command /synapse-status
├── scripts/
│   └── synapse_log.py          # Vista legible de la traza JSONL (filtros, --follow)
├── tests/
│   ├── conftest.py / support.py # Opción --laya, logs aislados, runners de hooks, repos git efímeros
│   ├── cases/<hook>.json       # Casos declarativos por hook
│   ├── unit/                   # Un módulo por hook + disposable targets, audit log, manifiesto
│   └── e2e/                    # Pipeline PreToolUse completo desde hooks.json sobre un repo git real
├── marketplace-entry.json      # Metadatos para el catálogo bypabloc/claude-plugins
├── README.md                   # Documentación orientada a usuarios y desarrolladores
└── AGENTS.md                   # Este manual técnico
```

---

## 3. Protocolo de Ciclo de Vida: `PreToolUse`

Claude Code invoca los scripts en `hooks/` pasando una carga útil JSON vía `stdin` antes de ejecutar cualquier herramienta.

### Formato de Entrada (`stdin`)
```json
{
  "tool_name": "Bash",
  "tool_input": {
    "command": "rm -f ./tmp/cache.json"
  },
  "cwd": "/ruta/al/proyecto"
}
```

### Respuestas Permitidas por el Protocolo

1. **Autorización Directa (`allow`):**
   - Código de salida: `0`
   - Salida estándar (`stdout`):
     ```json
     {
       "hookSpecificOutput": {
         "hookEventName": "PreToolUse",
         "permissionDecision": "allow",
         "permissionDecisionReason": "Operación en directorio temporal autorizada"
       }
     }
     ```
   - Opcionalmente, salida limpia vacía con código `0` autoriza por omisión.

2. **Solicitud de Confirmación Interactiva (`ask`):**
   - Código de salida: `0`
   - Salida estándar (`stdout`):
     ```json
     {
       "hookSpecificOutput": {
         "hookEventName": "PreToolUse",
         "permissionDecision": "ask",
         "permissionDecisionReason": "Modificación de configuración sensible requiere aprobación"
       }
     }
     ```
   - Claude Code pausa la ejecución y solicita confirmación explícita al usuario.

3. **Bloqueo Incondicional (`block`):**
   - Código de salida: `2`
   - Salida estándar de error (`stderr`): Razón técnica del bloqueo. Claude Code aborta la llamada a la herramienta e informa al usuario.

---

## 4. Políticas de Guardrails por Hook

### A. `block_dangerous.py` (Herramienta: `Bash`)
- **Auto-Allow:** Comandos destructivos restringidos a `./tmp/**`, `/tmp/claude-*`, artefactos de build (`dist/`, `build/`, `.cache/`, `node_modules/`, `target/`), rutas gitignoreadas o archivos (no directorios) sin seguimiento git.
- **Bloqueo determinista (exit 2), antes que todo lo demás:**
  - Cualquier escritura en `.git/` con comandos de archivos (`rm`, `mv`, `cp` hacia, `sed -i`, `tee`, `>`/`>>`, `find -delete`...). Los comandos `git` no se ven afectados.
  - Cualquier escritura fuera de la raíz del proyecto (toplevel git del `cwd`), siguiendo `cd` dentro del comando. Excepciones: scratchpad `/tmp/claude-*` y `/dev/null`/`/dev/std*`.
  - Ejecución de scripts remotos (`curl … | sh`), exfiltración de `~/.ssh`, `~/.aws`, `env` a la red, `authorized_keys`, shells reversas.
- **Confirmación determinista (`ask`)**, evaluada antes del auto-allow: force push, `reset --hard`, `filter-branch`, `DROP DATABASE`, `crontab -r`, `aws s3 rb`, `sudo rm`, `history -c`.
- **Laya:** solo escala a `ask`, nunca bloquea; recibe el comando sin comentarios ni cuerpos de heredoc; los comandos rutinarios (`gh pr|run|issue`, `git status|add|commit|push`...) no pasan por Laya.
- **Bloqueo Duro (Exit 2):**
  - Borrado de sistema o raíz (`rm -rf /`, `rm -rf ~`, `rm -rf $HOME`).
  - Borrado en `/tmp/` del sistema operativo (fuera de sesiones de Claude).
  - Comandos destructivos de disco (`mkfs`, `dd if=... of=/dev/sd*`, `> /dev/sd*`).
  - Ataques de denegación de servicio (`:(){ :|:& };:`).
  - Sobreescritura forzada de ramas maestras (`git push --force origin master/main`).
- **Confirmación (`ask`):** Eliminación de archivos fuente del proyecto fuera de carpetas temporales.

### B. `block_env_read.py` (Herramientas: `Read`, `Bash`)
- **Permitido:** Lectura de plantillas (`.env.example`, `.env.sample`, `*.dist`, `*.template`) e inyección vía `source .env`.
- **Bloqueo Duro (Exit 2):** Invocaciones a `Read` o comandos `cat`, `head`, `tail`, `awk`, `grep`, `strings`, `python`, `node` dirigidos a archivos `.env`.
- **Ofuscación:** Genera e imprime en `stderr` un esquema con las llaves y tipos/longitudes inferidas (sin exponer valores reales).

### C. `detect_secrets.py` (Herramientas: `Edit`, `Write`)
- **Permitido:** Placeholders evidentes (`your-api-key`, `REPLACE_ME`, `sample-key`), lecturas de variables (`os.environ.get`, `process.env`).
- **Bloqueo Duro (Exit 2):**
  - Llaves privadas criptográficas (`-----BEGIN RSA PRIVATE KEY-----`).
  - Tokens de proveedores: OpenAI (`sk-...`), Anthropic (`sk-ant-api03-...`), Google Gemini (`AIza...`), AWS (`AKIA...`), GitHub (`ghp_...`), Slack (`xoxb-...`), Stripe (`sk_live_...`).
  - Asignaciones explícitas de contraseñas en texto plano.
- **Confirmación (`ask`):** credenciales no catalogadas que Laya System 1 sospecha (`confidence >= 0.70`). Laya nunca bloquea en ningún hook.

### D. `protect_files.py` (Herramientas: `Edit`, `Write`)
- **Permitido:** Archivos de código fuente estándar y plantillas `.env.example`.
- **Bloqueo Duro (Exit 2):** Modificación o creación dentro de `.git/`, `.venv/` o lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `bun.lock`, `uv.lock`, `Cargo.lock`).
- **Confirmación (`ask`):** Archivos de configuración de Claude (`.claude/settings.json`, `.claude/hooks/**`).

---

## 5. Sistema de Logging y Auditoría

Cada ejecución de un hook deja una traza **JSONL, una línea por paso**, en un único directorio:

- **Ubicación:** `${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse/AAAA-MM-DD.jsonl` (`$SYNAPSE_LOG_DIR` la reemplaza; lo usan los tests).
- **Campos comunes:** `ts`, `run` (id por ejecución), `session` (8 primeros caracteres del `session_id` de Claude Code), `hook`, `tool`, `step`.
- **Pasos:** `input` (cwd, motor, comando o ruta) → pasos intermedios (`regex.catastrophic`, `structure.*`, `regex.ask`, `laya`, `signatures`...) → `decision` (`allow | ask | block | pass`, `reason`, `ms`).
- **Privacidad:** el contenido de `Write`/`Edit` nunca se registra (solo `content_chars`); tokens con formato conocido y asignaciones de credenciales se enmascaran (`common.redact`).
- **API:** `read_hook_input("<hook>")` abre la traza, `log_step(step, **campos)` agrega pasos, `emit_decision`/`emit_block`/`record_audit_log` escriben la decisión.
- **Vista legible:** `python3 scripts/synapse_log.py [--since 30m] [--decision block] [--hook X] [--session Y] [--json] [--follow]`.

---

## 6. Procedimientos de Verificación y Testing

Toda modificación debe verificarse en ambos motores:

```bash
python3 -m pytest                  # Fallback determinístico: unit + e2e
python3 -m pytest --laya gpu       # Laya System 1 en CUDA (--laya cpu para CPU)
claude plugin validate --strict .  # Validación oficial del plugin
```

- Casos nuevos de un hook: agregarlos a `tests/cases/<hook>.json` (no requiere código).
- Repos git de prueba: `support.make_git_repo` los crea en `.test_repos/` (fuera de `tmp/`, porque cualquier segmento `/tmp/` vuelve desechable la ruta completa).
- Secretos falsos que GitHub push protection rechaza: usar marcadores de `support.PLACEHOLDERS`.

---

## 7. Reglas Estrictas de Desarrollo

1. **Archivos Temporales:** Usar **únicamente** `./tmp/` dentro de la raíz del proyecto. **Nunca** utilizar `/tmp/` del sistema operativo para archivos del proyecto o pruebas.
2. **Eliminación Segura:** Usar siempre `rm -f` o helpers de Python seguros (`shutil.rmtree` con control de errores).
3. **Cero Tolerancia a Fallos de Tipos y Linter:** Todo código Python debe tener tipado estricto (`from __future__ import annotations`, type hints explícitos).
4. **Resiliencia de Inferencia:** Todo llamado a `Router.predict` de Laya debe estar encapsulado en bloques `try...except` que degraden de forma limpia al fallback determinístico sin interrumpir a Claude Code.
5. **Idioma y Estilo:** Documentación, mensajes de commit y logs en **español latinoamericano neutro**. Símbolos de código, variables y nombres de funciones en inglés técnico. Commits en formato **Conventional Commits**.
