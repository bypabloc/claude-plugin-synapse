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
│   ├── common.py               # Cliente del daemon de Laya, alcance del proyecto, variables de shell, logs
│   ├── laya_daemon.py          # Daemon que mantiene Laya en la GPU (socket Unix, apagado por inactividad)
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
- **Auto-Allow recuperable:** Borrado de archivos o directorios commiteados sin ningún cambio local (ni modificados, ni en staging, ni sin seguimiento; lo ignorado solo si es cache regenerable). `git checkout HEAD -- <ruta>` los restaura idénticos, y el hook se lo recuerda a la sesión vía `additionalContext` (regla `structure.git_recoverable_delete`).
- **Bloqueo determinista (exit 2), antes que todo lo demás:**
  - Cualquier escritura fuera del entorno git del proyecto, siguiendo `cd` dentro del comando. Las raíces (`common.allowed_roots`) salen de `CLAUDE_PROJECT_DIR` (el `cwd` si no está definida): su toplevel git, el superproyecto si es un submódulo y todos los worktrees del repo. El `cwd` del hook nunca amplía el alcance, y un repo en `~` o `/` tampoco. Excepciones: scratchpad `/tmp/claude-*` y `/dev/null`/`/dev/std*`.
  - Las variables se resuelven antes de decidir (`common.expand_word`): asignaciones y `for x in ...` del propio comando, luego el entorno, con `${X:-defecto}`. `p=/home/x; rm -rf "$p"` se bloquea; `for p in *; do rm -rf "$p"; done` dentro de `tmp/` se auto-aprueba. Lo no resoluble (`$(cmd)`, `` `cmd` ``, `$1`, `${x#y}`, `read x`) no se bloquea: solo pierde el auto-allow. Un glob como `.*` se evalúa como `..`.
  - `rm link` no sigue el symlink (borra el enlace); `rm link/` y las escrituras sí lo siguen.
  - Ejecución de scripts remotos (`curl … | sh`), exfiltración de `~/.ssh`, `~/.aws`, `env` a la red, `authorized_keys`, shells reversas.
  - Escritura de archivos con contenido redactado por el agente (`agent_authored_write`): `echo`/`printf` redirigidos, `cat`/`tee` con heredoc, `sed -i`/`perl -i`, scripts `python3 - <<EOF` o `-c`/`-e` que escriben archivos. Se debe usar `Write`/`Edit`. Permitido: salida de herramientas (`pytest > tmp/log`, `cmd | tee log`, `git`, builds) y `echo "exit=$?" >> log` (solo expansiones de shell).
- **Confirmación determinista (`ask`) por `.git`:** cualquier comando cuyos argumentos (o variables) apunten a una carpeta `.git`, sea lectura o escritura (`cat .git/HEAD`, `rm -rf .git`, `d=.git; rm -rf $d`). No cuentan los valores de flags de exclusión (`--exclude .git`, `--glob '!.git'`, `-name .git`) ni `.gitignore`/`.github`. Los bloqueos previos (escribir desde Bash, fuera del proyecto) siguen aplicando primero.
- **Confirmación determinista (`ask`)**, evaluada antes del auto-allow: force push, `reset --hard`, `filter-branch`, `DROP DATABASE`, `crontab -r`, `aws s3 rb`, `sudo rm`, `history -c`.
- **Regla general: lo que el código puede decidir se decide antes de Laya.** Laya solo ve lo ambiguo. Lo vigila `python3 scripts/benchmark.py` (calidad en el banco de evaluación, consultas evitadas en la traza real, latencia y CPU/GPU); el recall de riesgosos no debe bajar respecto de "antes".
  - `block_dangerous`: no consultan a Laya los comandos rutinarios (`gh pr|run|issue`, `git status|add|commit|push`...) ni los pipelines de solo lectura (`is_read_only`: `ls`, `rg`, `cat`, `sed -n`, `find` sin `-exec`, `git log|diff|show`...) cuyas rutas caen dentro del proyecto, ni la sincronización de lockfiles (`is_lockfile_sync`: `uv lock|sync`, `bun|pnpm install` sin nombres de paquete, sin `-g` y con `--directory`/`-C` dentro del proyecto; `uv add`, `uv run` y `bun install pkg` sí pasan por Laya). Los binarios invocados por ruta, `less`, `set`, las lecturas fuera del proyecto (`/etc`, `/tmp`, `~`) y las sustituciones `$(...)` sí pasan por Laya. Un borrado permanente pide confirmación directamente, sin Laya.
  - `protect_files`: archivos de credenciales por nombre o extensión (`.key`, `.pem`, `id_rsa`, `.npmrc`, `*.tfstate`, `credentials.json`, `kubeconfig`...) → `ask` determinista. Laya solo evalúa nombres con indicios sensibles no concluyentes (`secret_settings.py`).
  - `detect_secrets`: Laya solo recibe las líneas con literales candidatos (alta entropía con letras y dígitos, o URL con contraseña). Sin candidatos no hay consulta.
  - `block_env_read`: solo cuentan archivos `.env` reales entre los argumentos (no `os.environ`, `process.env.X`). Los usos que no muestran valores (`ls`, `test -f`, `cp`, `git check-ignore`, `--env-file`) pasan; el código embebido (heredoc, `-c`) que abre un `.env` se bloquea.
- **Laya:** solo escala a `ask`, nunca bloquea.
- **Atribución obligatoria:** todo `block`/`ask`/`allow` sale por `emit_block`/`emit_decision`, que exigen `decided_by` (`python` | `laya`) y `rule`. En pantalla: `Synapse · <hook> (python|laya) · regla <rule>: <motivo>` (stderr en los bloqueos, `permissionDecisionReason` en ask/allow). En la traza, el paso `decision` lleva `decided_by`, `rule`, `reason` y `evidence` (patrón, rutas, puntaje y umbral de Laya, confianza). Un `pass` queda atribuido a `laya` si se consultó a Laya y no objetó, o a `python` en otro caso.
- **Todo run cierra con `decision`:** los `main` se ejecutan con `common.run_main`; las salidas tempranas quedan como `pass` con regla `exit.early` y un error interno se registra (step `error`, regla `hook.error`) antes de relanzarse. Los errores de Laya se registran (`laya result=error`) en los cuatro hooks.
- **Retención de la traza:** al crear el archivo del día se borran los `AAAA-MM-DD.jsonl` más antiguos que `SYNAPSE_LOG_RETENTION_DAYS` (30 por defecto; `0` conserva todo).
  - Entrada: `command_evidence()` (comando sin comentarios ni cuerpos de heredoc + efectos detectados: qué borra, escribe, lee o envía).
  - Pregunta binaria `DANGER_QUESTION` con claves neutras `A`/`B`, siempre sobre el checkpoint `english`.
  - Modelo: delta afinado (4 capas superiores + cabeza) en `models/laya-block-dangerous-delta.safetensors` (Git LFS) y calibración Platt en `models/laya-block-dangerous.json`. Si el delta es solo un puntero LFS, usa el perfil `zeroshot` calibrado.
- **Daemon de Laya (`hooks/laya_daemon.py`):** los hooks nunca importan torch; piden la inferencia por un socket Unix (`$XDG_RUNTIME_DIR/synapse/`, o `~/.cache/synapse/`) a un proceso que mantiene el checkpoint en la GPU. El primer hook lo levanta (~5 s), después cada inferencia toma ~30 ms. Las inferencias se serializan con un lock. El delta afinado se aplica solo durante la petición de `block_dangerous` y se revierte, así que los demás hooks ven el checkpoint base. Tras `SYNAPSE_LAYA_IDLE_SECONDS` sin uso (1200 por defecto) termina y libera la VRAM. Hay un daemon por instalación del plugin y por modo (`gpu`/`cpu`). Para detenerlo: `python3 hooks/laya_daemon.py stop gpu`. Los scripts de evaluación usan `laya_daemon.local_router()` en el mismo proceso.
  - Re-entrenar: `scripts/laya_eval.py` (dataset, scoring, calibración, `export`) + `scripts/laya_finetune.py`. Cambiar la pregunta o la entrada invalida delta y calibración.
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
