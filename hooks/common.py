"""Módulo central y utilidades compartidas para los hooks de seguridad de Synapse.

Contexto y Propósito:
  Provee la infraestructura compartida para los hooks de seguridad de Claude Code:
  - Inicialización singleton del Router Laya (precarga y optimización de memoria).
  - Autodetección de aceleradores (CUDA/GPU vs. CPU) y política de fallback automático.
  - Gestión de entrada y salida del protocolo PreToolUse (stdin JSON, stdout JSON 'allow'/'ask', stderr exit 2).
  - Normalización de rutas y autorización incondicional de temporales (./tmp/**, /tmp/claude-*).
  - Inferencia y ofuscación de variables de entorno (describe formatos sin exponer secretos).
  - Auditoría persistente en logs (~/.claude/logs/security_hooks.log y logs/ locales).

Ejemplos de Uso:
  >>> from common import is_disposable_target, should_use_laya, infer_format
  >>> is_disposable_target("./tmp/cache.json", cwd="/proyecto")
  True
  >>> infer_format("postgres://user:pass@host:5432/db")
  'URL, 33 caracteres'
  >>> should_use_laya()
  True  # si CUDA está disponible y no se especificó --fallback
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Desactivar TensorFlow Abseil para evitar bloqueos en imports
os.environ["USE_TF"] = "0"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# Importación resiliente de Laya y PyTorch
try:
    import torch
    import laya
    from laya import Router
    _LAYA_AVAILABLE = True
except ImportError:
    torch = None
    laya = None
    Router = None
    _LAYA_AVAILABLE = False

# Instancia singleton perezosa del Router
_ROUTER_INSTANCE: Any = None

# Segmentos de archivos y carpetas temporales autorizados automáticamente
TMP_SEGMENT_RE = re.compile(r"(^|/)tmp(/|$)")
SCRATCHPAD_RE = re.compile(r"^/tmp/claude-[^/]*(/|$)")
BUILD_ARTIFACT_EXTS = {".pyc", ".pyo", ".pyd", ".tmp", ".log", ".cache", ".bak"}
BUILD_ARTIFACT_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build"}

# Patrones para archivos .env y sufijos de plantillas
ENV_NAME_RE = re.compile(r"^\.env(\..+)?$|\.env$")
TEMPLATE_SUFFIXES = (".example", ".sample", ".dist", ".template")

# Patrones de archivos protegidos y de confirmación requerida
PROTECTED_PATTERNS = (
    ".env",
    "package-lock.json",
    "bun.lock",
    "yarn.lock",
    "uv.lock",
    ".git/",
    "node_modules/",
    ".venv/",
)
ASK_PATTERNS = (
    ".claude/settings.json",
    ".claude/hooks/",
)


def is_cuda_available() -> bool:
    """Verifica si CUDA/GPU está disponible de forma segura."""
    if not _LAYA_AVAILABLE or torch is None:
        return False
    try:
        return torch.cuda.is_available()
    except Exception:
        return False


def should_use_laya() -> bool:
    """Determina si se debe utilizar Laya System 1 o activar el fallback de scripts originales.

    Reglas:
      - Si Laya no está instalado en el entorno: False (fallback determinístico).
      - Si se especifica flag --fallback: False (usa los scripts originales de inmediato).
      - Si se especifica flag --cpu: True (fuerza inferencia de Laya en CPU).
      - Si se especifica flag --gpu: True si CUDA está disponible, False si no hay GPU (fallback).
      - Sin flags: Autodetecta CUDA. Si hay GPU activa Laya; si no hay GPU, activa el fallback.
    """
    if not _LAYA_AVAILABLE:
        return False
    if "--fallback" in sys.argv:
        return False
    if "--cpu" in sys.argv:
        return True
    if "--gpu" in sys.argv:
        return is_cuda_available()
    return is_cuda_available()


def get_configured_device() -> str:
    """Determina el acelerador a utilizar según argv, variables de entorno o hardware detectado."""
    if not _LAYA_AVAILABLE or torch is None:
        return "cpu"

    if "--cpu" in sys.argv:
        physical_cores = os.cpu_count() or 4
        target_threads = max(1, physical_cores // 2 if physical_cores > 4 else physical_cores)
        torch.set_num_threads(target_threads)
        return "cpu"

    if "--gpu" in sys.argv:
        return "cuda" if is_cuda_available() else "cpu"

    env_device = os.environ.get("LAYA_DEVICE", "").lower()
    if env_device in {"cuda", "cpu", "mps"}:
        return env_device

    return "cuda" if is_cuda_available() else "cpu"


def get_laya_router() -> Any:
    """Retorna una instancia singleton precargada de Laya Router."""
    global _ROUTER_INSTANCE
    if not _LAYA_AVAILABLE or Router is None:
        raise RuntimeError("Laya System 1 no está disponible en este entorno. Opere en modo fallback.")
    if _ROUTER_INSTANCE is None:
        device = get_configured_device()
        _ROUTER_INSTANCE = Router(preload=True, device=device)
    return _ROUTER_INSTANCE


# Archivos de log de auditoría persistentes en la PC
LOG_PATHS = [
    Path.home() / ".claude" / "logs" / "security_hooks.log",
    Path(__file__).resolve().parent.parent / "logs" / "security_hooks.log",
]

_STATUS_NOTIFIED = False


def notify_device_status() -> None:
    """Emite información o advertencias sobre el modo de cómputo en stderr.

    - Si cae de GPU a CPU: Imprime sugerencia informativa para habilitar aceleración.
    - Si rechaza GPU y CPU (modo Fallback): Imprime advertencia indicando que la IA no funciona.
    """
    global _STATUS_NOTIFIED
    if _STATUS_NOTIFIED:
        return
    _STATUS_NOTIFIED = True

    if not should_use_laya():
        sys.stderr.write(
            "⚠️ [WARNING] Motor Laya System 1 (GPU/CPU) desactivado o no disponible.\n"
            "   El script opera en modo FALLBACK determinístico (solo reglas estáticas).\n"
            "   Para activar la IA, instale PyTorch con soporte CUDA/CPU y verifique las dependencias de Laya.\n"
        )
    else:
        device = get_configured_device()
        if device == "cpu":
            sys.stderr.write(
                "ℹ️ [INFO] Ejecutando Synapse (Laya System 1) en CPU.\n"
                "   Sugerencia: Para acelerar la inferencia a ~25ms, configure aceleración CUDA/GPU.\n"
            )


def record_audit_log(
    action: str,  # "ALLOW" | "ASK" | "BLOCKED" | "PASSED"
    hook_name: str = "hook",
    tool_name: str = "",
    target: str = "",
    reason: str = "",
) -> None:
    """Registra de forma persistente la decisión del hook en los archivos de log de la PC."""
    try:
        device_mode = "FALLBACK"
        if should_use_laya():
            device_mode = f"LAYA-{get_configured_device().upper()}"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_target = " ".join(target.split())[:140] if target else "(none)"
        clean_reason = " ".join(reason.split())[:200]
        log_line = (
            f"[{timestamp}] [{action:7s}] [{device_mode:11s}] [{hook_name:17s}] "
            f"Tool: {tool_name:5s} | Target: '{clean_target}' | Reason: {clean_reason}\n"
        )

        for p in LOG_PATHS:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(log_line)
            except Exception:
                pass
    except Exception:
        pass


def read_hook_input() -> dict[str, Any]:
    """Lee y parsea la carga útil JSON enviada por Claude Code en stdin y notifica estado de hardware."""
    notify_device_status()
    try:
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def emit_decision(
    decision: str,
    reason: str,
    hook_name: str = "hook",
    tool_name: str = "",
    target: str = "",
) -> None:
    """Emite una decisión formal ('allow' o 'ask') al stdout en formato PreToolUse y la audita en log."""
    action = "ALLOW" if decision.lower() == "allow" else "ASK"
    record_audit_log(action, hook_name, tool_name, target, reason)
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload))
    sys.exit(0)


def emit_block(
    reason: str,
    exit_code: int = 2,
    hook_name: str = "hook",
    tool_name: str = "",
    target: str = "",
) -> None:
    """Bloquea la ejecución de la herramienta con un mensaje en stderr y la audita en log."""
    record_audit_log("BLOCKED", hook_name, tool_name, target, reason)
    sys.stderr.write(f"🚫 BLOCKED: {reason}\n")
    sys.exit(exit_code)


def resolve_path(path: str, cwd: str) -> str:
    """Normaliza y resuelve una ruta contra el directorio de trabajo."""
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path(cwd) / p
        return os.path.normpath(str(p))
    except Exception:
        return path


def is_system_tmp(path: str, cwd: str) -> bool:
    """Detecta si la ruta apunta a /tmp del sistema operativo fuera del scratchpad de sesión."""
    resolved = resolve_path(path, cwd)
    if resolved == "/tmp" or resolved.startswith("/tmp/"):
        return not bool(SCRATCHPAD_RE.match(resolved))
    return False


def is_git_ignored(path: str, cwd: str) -> bool:
    """Consulta a git check-ignore si la ruta está ignorada en el repositorio del proyecto."""
    try:
        top_proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if top_proc.returncode != 0:
            return False
        toplevel = os.path.realpath(top_proc.stdout.strip())
        claude_dir = os.path.realpath(str(Path.home() / ".claude"))
        if toplevel == claude_dir or toplevel == os.path.realpath(str(Path.home())):
            return False

        res = subprocess.run(
            ["git", "-C", cwd, "check-ignore", "-v", "--", path],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode != 0:
            return False
        out = res.stdout.strip()
        if any(ign in out for ign in ["personal/*", "cache/*", "plugins/*"]):
            return False
        return True
    except Exception:
        return False



def is_disposable_target(path: str, cwd: str) -> bool:
    """Verifica si una ruta es un archivo o directorio temporal desechable.

    Aprobado automáticamente:
      1. Rutas bajo ./tmp/ del proyecto.
      2. Scratchpad de sesión (/tmp/claude-*).
      3. Directorios de build conocidos (dist, build, __pycache__, .pytest_cache).
      4. Extensiones efímeras (*.pyc, *.tmp, *.log, *.cache).
      5. Archivos marcados por .gitignore.
    """
    if is_system_tmp(path, cwd):
        return False

    resolved = resolve_path(path, cwd)
    p = Path(resolved)

    # 1. Carpeta tmp local del proyecto
    if TMP_SEGMENT_RE.search(resolved):
        return True

    # 2. Scratchpad de sesión
    if SCRATCHPAD_RE.match(resolved):
        return True

    # 3. Artefactos de build / caches
    if any(part in BUILD_ARTIFACT_DIRS for part in p.parts):
        return True

    # 4. Extensiones de archivos temporales
    if p.suffix.lower() in BUILD_ARTIFACT_EXTS:
        return True

    # 5. Git ignored
    if is_git_ignored(path, cwd):
        return True

    return False


def extract_bash_tokens(command: str) -> list[str]:
    """Extrae sub-comandos separados por operadores de shell (&&, ||, ;, |)."""
    command = re.sub(r"\\\n", " ", command)
    sep = re.compile(r"(\|\||&&|;|\||\n)")
    parts = sep.split(command)
    return [p.strip() for p in parts if p and not sep.fullmatch(p) and p.strip()]


def parse_command_targets(subcmd: str) -> tuple[str, list[str]]:
    """Obtiene el nombre del comando base y sus argumentos de ruta."""
    try:
        tokens = shlex.split(subcmd, posix=True)
    except ValueError:
        return "", []
    if not tokens:
        return "", []
    cmd = os.path.basename(tokens[0])
    args = tokens[1:]
    paths: list[str] = [tok for tok in args if not tok.startswith("-")]
    return cmd, paths


def is_env_file(path: str) -> bool:
    """Verifica si la ruta apunta a un archivo de variables de entorno (excluyendo plantillas)."""
    name = os.path.basename(path)
    if not ENV_NAME_RE.search(name):
        return False
    return not name.endswith(TEMPLATE_SUFFIXES)


def infer_format(value: str) -> str:
    """Describe el formato/tipo aparente del value SIN revelar su contenido sensible."""
    v = value.strip().strip("'\"")
    length = len(v)
    if not v:
        return "vacio"
    if v.lower() in {"true", "false"}:
        return "booleano"
    if re.fullmatch(r"-?\d+", v):
        return f"numerico, {length} digitos"
    if re.fullmatch(r"-?\d+\.\d+", v):
        return "numerico decimal"
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", v):
        return f"URL, {length} caracteres"
    return f"texto, {length} caracteres"


def extract_keys_with_format(file_path: str) -> list[str]:
    """Lee KEY=value por línea y devuelve 'KEY=<formato>' sin exponer ningún valor real."""
    try:
        text = Path(file_path).read_text(errors="replace")
    except Exception as exc:
        return [f"(no se pudo leer el archivo para inspeccionar keys: {exc})"]

    results: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", stripped)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        results.append(f"{key}=<{infer_format(value)}>")
    return results


def build_env_block_message(file_path: str) -> str:
    """Construye un mensaje explicativo detallando cómo usar las variables sin leerlas."""
    keys = extract_keys_with_format(file_path)
    lines = [
        f"🚫 BLOCKED: Lectura directa de archivo de credenciales: {file_path}",
        "No se permite leer el contenido de archivos .env con Read/cat/head/tail.",
        "Si necesitas USAR las variables (no verlas), usa en Bash:",
        f"  set -a; source {file_path}; set +a",
        "",
        "Keys disponibles (formato inferido, valores NUNCA mostrados):",
    ]
    if keys:
        lines.extend(f"  {k}" for k in keys)
    else:
        lines.append("  (no se detectaron pares KEY=value)")
    return "\n".join(lines)
