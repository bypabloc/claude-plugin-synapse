# Synapse Security Guardrails & Policy Guidelines

<!-- ====================================================================== -->
<!-- Synapse: Real-Time Guardrails & PreToolUse Interceptors                -->
<!-- Powered by Laya System 1 + Deterministic Zero-Latency Fallback         -->
<!-- ====================================================================== -->

## 1. Gestión de Archivos Temporales y Desechables
- **Auto-Aprobación Incondicional**: Cualquier operación de eliminación (`rm`, `rm -rf`, `rmdir`) o modificación sobre rutas bajo `./tmp/**` dentro del espacio de trabajo del proyecto está **autorizada de inmediato sin interrupciones**.
- **Scratchpad de Sesión**: Archivos bajo `/tmp/claude-*` se consideran temporales legítimos de la sesión de Claude Code.
- **Prohibición de `/tmp` del Sistema**: Salvo el scratchpad de sesión, está estrictamente prohibido usar `/tmp` del sistema operativo; todo archivo temporal debe residir en `./tmp/` del proyecto.
- **Artefactos de Build y Caches**: Se autoriza la limpieza de artefactos conocidos (`__pycache__`, `.pytest_cache`, `.ruff_cache`, `dist`, `build`, `*.pyc`, `*.log`, `*.cache`).

## 2. Protección de Credenciales y Archivos `.env`
- **Bloqueo de Lectura Directa**: Queda terminantemente bloqueada la lectura del contenido de archivos `.env*` a través de herramientas de lectura (`Read`) o utilidades de shell (`cat`, `head`, `tail`, `awk`, `sed`, `grep`, `cut`, `base64`, `xxd`).
- **Carga de Variables Permitida**: Para utilizar variables de entorno sin exponer sus secretos al contexto de la IA, use en Bash:
  ```bash
  set -a; source .env; set +a
  ```
- **Inferencia de Formato Segura**: Al interceptar un intento de lectura, Synapse reporta las claves detectadas con su formato inferido (`<URL, 32 caracteres>`, `<booleano>`, `<numérico, 4 dígitos>`, etc.) sin revelar jamás sus valores reales.
- **Plantillas Permitidas**: Los archivos de ejemplo (`.env.example`, `.env.sample`, `*.template`, `*.dist`) son editables libremente.

## 3. Detección Proactiva de Secretos
- **Inspección en `Edit` y `Write`**: Cada fragmento de texto o código a escribir es auditado antes de guardarse en disco.
- **Firmas Bloqueadas**: Claves de OpenAI (`sk-*`), claves de AWS (`AKIA*`), tokens de GitHub (`ghp_*`), tokens JWT (`eyJ...`), bloques de clave privada RSA/SSH/PGP y credenciales en texto plano.
- **Placeholders Permitidos**: Placeholders transparentes (`REPLACE_ME`, `your-api-key-here`, `<insert-key>`, `dummy`, accesos a `os.environ.get`) no disparan alertas.

## 4. Protección de Integridad del Repositorio
- **Archivos Críticos Inmutables**: Se bloquea la modificación arbitraria de lockfiles (`package-lock.json`, `bun.lock`, `yarn.lock`, `uv.lock`, `poetry.lock`) y directorios de infraestructura (`.git/`, `node_modules/`, `.venv/`).
- **Confirmación Requerida (`ask`)**: La modificación de configuraciones del agente (`.claude/settings.json`, `.claude/hooks/**`) requiere confirmación interactiva explícita del usuario.
