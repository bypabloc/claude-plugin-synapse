"""Módulo central y utilidades compartidas para los hooks de seguridad de Synapse.

Contexto y Propósito:
  Provee la infraestructura compartida para los hooks de seguridad de Claude Code:
  - Inicialización singleton del Router Laya (precarga y optimización de memoria).
  - Autodetección de aceleradores (CUDA/GPU vs. CPU) y política de fallback automático.
  - Gestión de entrada y salida del protocolo PreToolUse (stdin JSON, stdout JSON 'allow'/'ask', stderr exit 2).
  - Normalización de rutas y autorización incondicional de temporales (./tmp/**, /tmp/claude-*).
  - Inferencia y ofuscación de variables de entorno (describe formatos sin exponer secretos).
  - Traza JSONL de cada paso en ${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse/AAAA-MM-DD.jsonl
    (vista legible: scripts/synapse_log.py).

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
BUILD_ARTIFACT_EXTS = {
    ".pyc", ".pyo", ".pyd", ".tmp", ".log", ".cache", ".bak",
    ".coverage", ".tsbuildinfo", ".swp", ".swo",
}
BUILD_ARTIFACT_DIRS = {
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".coverage", ".nyc_output", ".turbo", "dist", "build", ".next",
    ".nuxt", ".angular", "target", "coverage", ".parcel-cache",
}

# Patrones para archivos .env y sufijos de plantillas
ENV_NAME_RE = re.compile(r"^\.env(\..+)?$|\.env$")
TEMPLATE_SUFFIXES = (".example", ".sample", ".dist", ".template")

# Patrones de archivos protegidos y de confirmación requerida
PROTECTED_PATTERNS = (
    ".env",
    "package-lock.json",
    "pnpm-lock.yaml",
    "bun.lock",
    "bun.lockb",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    ".git/",
    "node_modules/",
    ".venv/",
)
ASK_PATTERNS = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/hooks/",
    ".vscode/settings.json",
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


def get_log_dir() -> Path:
    """Directorio único de logs: $SYNAPSE_LOG_DIR (tests) o ${CLAUDE_CONFIG_DIR:-~/.claude}/logs/synapse."""
    override = os.environ.get("SYNAPSE_LOG_DIR")
    if override:
        return Path(override).expanduser()
    claude_dir = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(claude_dir).expanduser() / "logs" / "synapse"


def get_log_file(day: datetime | None = None) -> Path:
    return get_log_dir() / f"{(day or datetime.now()):%Y-%m-%d}.jsonl"


# Tokens con formato conocido y asignaciones de credenciales: nunca se escriben completos en el log
_REDACT_RES = [
    re.compile(r"(sk-ant-|sk-proj-|sk-|sk_live_|sk_test_|rk_live_|AKIA|ghp_|gho_|ghs_|github_pat_|xox[baprs]-|AIza|eyJ)[A-Za-z0-9_\-.]{6,}"),
    # Asignaciones (password=x, TOKEN: x) y flags (--password x); "auth token --user" no es un valor
    re.compile(r"(?i)\b(\w*(?:password|passwd|secret|token|api[_-]?key))(\s*[:=]\s*)(['\"]?)(?!\$)[^\s'\";&|]{4,}"),
    re.compile(r"(?i)(--(?:password|passwd|token|api[_-]?key|secret)\s+)(['\"]?)[^\s'\";&|-][^\s'\";&|]{3,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-.=]{8,}"),
]


def redact(text: str) -> str:
    text = _REDACT_RES[0].sub(lambda m: m.group(1) + "…[REDACTED]", text)
    text = _REDACT_RES[1].sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]", text)
    text = _REDACT_RES[2].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    return _REDACT_RES[3].sub(lambda m: m.group(1) + "[REDACTED]", text)


_TRACE: dict[str, Any] = {}
_MAX_FIELD = 2000


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        value = redact(value)
        return value if len(value) <= _MAX_FIELD else value[:_MAX_FIELD] + f"…(+{len(value) - _MAX_FIELD})"
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def start_trace(hook_name: str, data: dict[str, Any]) -> None:
    """Abre una ejecución: run_id propio, sesión de Claude Code y paso 'input'."""
    _TRACE.clear()
    _TRACE.update(
        run=os.urandom(4).hex(),
        session=str(data.get("session_id") or "-")[:8],
        hook=hook_name,
        tool=data.get("tool_name", ""),
        t0=datetime.now(),
    )
    tool_input = data.get("tool_input", {}) or {}
    summary: dict[str, Any] = {"cwd": data.get("cwd", "")}
    if "command" in tool_input:
        summary["command"] = tool_input["command"]
    if "file_path" in tool_input:
        summary["file_path"] = tool_input["file_path"]
    # El contenido de Write/Edit nunca se registra: puede ser justamente el secreto que se bloquea
    for key in ("content", "new_string"):
        if key in tool_input:
            summary[f"{key}_chars"] = len(tool_input[key] or "")
    summary["engine"] = engine_mode()
    log_step("input", **summary)


def engine_mode() -> str:
    try:
        return f"laya-{get_configured_device()}" if should_use_laya() else "fallback"
    except Exception:
        return "fallback"


def log_step(step: str, **fields: Any) -> None:
    """Escribe un paso de la ejecución actual como una línea JSON (append, tolerante a fallos)."""
    try:
        now = datetime.now()
        record = {
            "ts": now.isoformat(timespec="milliseconds"),
            "run": _TRACE.get("run", "-"),
            "session": _TRACE.get("session", "-"),
            "hook": _TRACE.get("hook", "-"),
            "tool": _TRACE.get("tool", ""),
            "step": step,
            **_clean(fields),
        }
        if step == "decision" and "t0" in _TRACE:
            record["ms"] = round((now - _TRACE["t0"]).total_seconds() * 1000, 1)
        path = get_log_file(now)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


_STATUS_NOTIFIED = False


def notify_device_status() -> None:
    """Emite información o advertencias sobre el modo de cómputo en stderr solo en modo debug/verbose."""
    global _STATUS_NOTIFIED
    if _STATUS_NOTIFIED:
        return
    _STATUS_NOTIFIED = True

    # Evitar ruido en stderr en sesiones estándar de Claude Code salvo que se pida debug/verbose
    if not (os.environ.get("SYNAPSE_DEBUG") == "1" or "--verbose" in sys.argv or "--debug" in sys.argv):
        return

    if not should_use_laya():
        sys.stderr.write(
            "⚠️ [WARNING] Motor Laya System 1 (GPU/CPU) desactivado o no disponible.\n"
            "   El script opera en modo FALLBACK determinístico (solo reglas estáticas).\n"
        )
    else:
        device = get_configured_device()
        if device == "cpu":
            sys.stderr.write(
                "ℹ️ [INFO] Ejecutando Synapse (Laya System 1) en CPU.\n"
                "   Sugerencia: Para acelerar la inferencia a ~25ms, configure aceleración CUDA/GPU.\n"
            )


# "ALLOW" en llamadas directas de los hooks significa "sin opinión" (exit 0 sin JSON): Claude Code decide
_DECISION_NAMES = {"ALLOW": "pass", "PASSED": "pass", "ASK": "ask", "BLOCKED": "block", "allow": "allow", "ask": "ask"}


def record_audit_log(
    action: str,
    hook_name: str = "hook",
    tool_name: str = "",
    target: str = "",
    reason: str = "",
) -> None:
    """Registra el paso final 'decision' de la ejecución actual."""
    if not _TRACE:
        _TRACE.update(run=os.urandom(4).hex(), session="-", hook=hook_name, tool=tool_name, t0=datetime.now())
    log_step("decision", decision=_DECISION_NAMES.get(action, action.lower()), target=target, reason=reason)


def read_hook_input(hook_name: str = "hook") -> dict[str, Any]:
    """Lee el JSON de stdin enviado por Claude Code y abre la traza de la ejecución."""
    notify_device_status()
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        data = {}
    start_trace(hook_name, data)
    return data


def emit_decision(
    decision: str,
    reason: str,
    hook_name: str = "hook",
    tool_name: str = "",
    target: str = "",
) -> None:
    """Emite una decisión formal ('allow' o 'ask') al stdout en formato PreToolUse y la audita en log."""
    record_audit_log(decision.lower(), hook_name, tool_name, target, reason)
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
        p = Path(os.path.expandvars(path)).expanduser()
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

        # Ruta resuelta: git no expande '~', y '~/.ssh/x' literal coincidía con el patrón '*~' del .gitignore
        res = subprocess.run(
            ["git", "-C", cwd, "check-ignore", "-v", "--", resolve_path(path, cwd)],
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


def is_git_untracked_file(path: str, cwd: str) -> bool:
    """Detecta un archivo (no directorio) existente que git nunca ha rastreado en el repo del proyecto.

    Cubre el caso típico del agente: crea un archivo de prueba y lo borra segundos después.
    Los directorios quedan fuera porque pueden contener mucho trabajo nuevo sin commit.
    """
    resolved = resolve_path(path, cwd)
    if not os.path.isfile(resolved) or os.path.islink(resolved):
        return False
    try:
        parent = os.path.dirname(resolved)
        top_proc = subprocess.run(
            ["git", "-C", parent, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if top_proc.returncode != 0:
            return False
        toplevel = os.path.realpath(top_proc.stdout.strip())
        home = Path.home()
        if toplevel in (os.path.realpath(str(home / ".claude")), os.path.realpath(str(home))):
            return False
        res = subprocess.run(
            ["git", "-C", parent, "ls-files", "--error-unmatch", "--", resolved],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return res.returncode != 0
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
      6. Archivos sin seguimiento git (no directorios).
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

    # 6. Archivo sin seguimiento git (nunca fue parte del historial)
    if is_git_untracked_file(path, cwd):
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
    # Asignaciones de entorno (VAR=x cmd) y envoltorios (sudo rm ...) no son el comando real
    while tokens and (re.match(r"^[A-Za-z_]\w*=", tokens[0]) or tokens[0] in COMMAND_WRAPPERS):
        tokens = tokens[1:]
        while tokens and tokens[0].startswith("-"):  # flags del envoltorio (sudo -E, nice -n 5)
            tokens = tokens[1:]
            if tokens and tokens[0].isdigit():
                tokens = tokens[1:]
    if not tokens:
        return "", []
    cmd = os.path.basename(tokens[0])
    paths: list[str] = []
    skip_next = False
    for tok in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if REDIRECT_OP_RE.fullmatch(tok) or tok in {"<", "<<", "<<<"}:
            skip_next = True
            continue
        if REDIRECT_GLUED_RE.match(tok) or tok.startswith(("-", "<")):
            continue
        paths.append(tok)
    return cmd, paths


COMMAND_WRAPPERS = {"sudo", "doas", "nohup", "time", "command", "exec", "nice", "ionice", "env"}
REDIRECT_OP_RE = re.compile(r"(\d|&)?>{1,2}\|?")
REDIRECT_GLUED_RE = re.compile(r"^(\d|&)?>{1,2}\|?(.+)$")
HARMLESS_DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/tty", "/dev/fd/")


def redirect_targets(subcmd: str) -> list[str]:
    """Rutas a las que un sub-comando redirige su salida (>, >>, 2>, &>). Ignora duplicaciones de fd (>&2)."""
    try:
        tokens = shlex.split(subcmd, posix=True)
    except ValueError:
        return []
    targets: list[str] = []
    for i, tok in enumerate(tokens):
        if REDIRECT_OP_RE.fullmatch(tok) and i + 1 < len(tokens):
            targets.append(tokens[i + 1])
            continue
        glued = REDIRECT_GLUED_RE.match(tok)
        if glued:
            targets.append(glued.group(2))
    return [t for t in targets if not t.startswith("&")]


def project_root(cwd: str) -> str:
    """Raíz del entorno de ejecución: toplevel git del cwd, o el propio cwd si no es un repo."""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=2
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return os.path.realpath(proc.stdout.strip())
    except Exception:
        pass
    return os.path.realpath(cwd)


def is_outside_project(path: str, cwd: str, root: str) -> bool:
    """True si la ruta resuelta cae fuera de root. Excepciones: scratchpad de sesión y dispositivos inocuos.

    Una ruta con variables sin resolver ($DIR/x) no es decidible aquí: se trata como interna y queda a cargo
    de las demás reglas (los borrados no desechables piden confirmación).
    """
    resolved = resolve_path(path, cwd)
    if "$" in resolved:
        return False
    if SCRATCHPAD_RE.match(resolved) or resolved.startswith(HARMLESS_DEVICES):
        return False
    real = os.path.realpath(resolved)
    return not (real == root or real.startswith(root.rstrip(os.sep) + os.sep))


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
    if re.match(r"^eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+$", v):
        return f"token JWT, {length} caracteres"
    if re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", v):
        return "UUID"
    if re.fullmatch(r"[0-9a-fA-F]{32,64}", v):
        return f"hash hex, {length} caracteres"
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
