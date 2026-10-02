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
├── tests/
│   ├── test_manifest.py        # Verificación estructural de manifiesto y componentes
│   ├── test_logging.py         # Verificación de auditoría segregada (SYNAPSE_LOG_DIR)
│   └── test_hooks.py           # Suite de 36 pruebas para los 4 hooks (GPU/CPU/Fallback)
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
- **Auto-Allow:** Comandos destructivos restringidos a `./tmp/**`, `/tmp/claude-*` o artefactos de build (`dist/`, `build/`, `.cache/`, `node_modules/`, `target/`).
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
  - Tokens y credenciales no catalogados detectados por Laya System 1 (`confidence >= 0.70`).

### D. `protect_files.py` (Herramientas: `Edit`, `Write`)
- **Permitido:** Archivos de código fuente estándar y plantillas `.env.example`.
- **Bloqueo Duro (Exit 2):** Modificación o creación dentro de `.git/`, `.venv/` o lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `bun.lock`, `uv.lock`, `Cargo.lock`).
- **Confirmación (`ask`):** Archivos de configuración de Claude (`.claude/settings.json`, `.claude/hooks/**`).

---

## 5. Sistema de Logging y Auditoría

La función `record_audit_log` en `hooks/common.py` registra de manera uniforme todos los eventos:

- **Rutas de Almacenamiento (orden de resolución):**
  1. `$SYNAPSE_LOG_DIR/security_hooks.log` (prioridad máxima; mandatorio para testing).
  2. `$CLAUDE_PLUGIN_DATA/logs/security_hooks.log` (runtime de Claude Code).
  3. `~/.claude/logs/security_hooks.log` (directorio global).
  4. `./logs/security_hooks.log` (local en repositorio).
- **Formato de Registro:**
  ```
  [YYYY-MM-DD HH:MM:SS] [ACCION ] [MODO_DISPOSITIVO] [NOMBRE_HOOK      ] Tool: HERRAMIENTA | Target: 'OBJETIVO' | Reason: MOTIVO
  ```

---

## 6. Procedimientos de Verificación y Testing

Toda modificación debe verificarse ejecutando los tres niveles de prueba:

```bash
# 1. Integridad estructural y auditoría segregada en ./tmp/test_audit_logs/
python3 -m unittest discover tests

# 2. Batería completa de guardrails en GPU (CUDA)
python3 tests/test_hooks.py --gpu

# 3. Batería completa en modo Fallback determinístico
python3 tests/test_hooks.py --fallback

# 4. Batería en subprocesos independientes
python3 tests/test_hooks.py --fallback --subprocess

# 5. Validación oficial del plugin
claude plugin validate --strict .
```

---

## 7. Reglas Estrictas de Desarrollo

1. **Archivos Temporales:** Usar **únicamente** `./tmp/` dentro de la raíz del proyecto. **Nunca** utilizar `/tmp/` del sistema operativo para archivos del proyecto o pruebas.
2. **Eliminación Segura:** Usar siempre `rm -f` o helpers de Python seguros (`shutil.rmtree` con control de errores).
3. **Cero Tolerancia a Fallos de Tipos y Linter:** Todo código Python debe tener tipado estricto (`from __future__ import annotations`, type hints explícitos).
4. **Resiliencia de Inferencia:** Todo llamado a `Router.predict` de Laya debe estar encapsulado en bloques `try...except` que degraden de forma limpia al fallback determinístico sin interrumpir a Claude Code.
5. **Idioma y Estilo:** Documentación, mensajes de commit y logs en **español latinoamericano neutro**. Símbolos de código, variables y nombres de funciones en inglés técnico. Commits en formato **Conventional Commits**.
