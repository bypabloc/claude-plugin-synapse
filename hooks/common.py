"""Módulo central y utilidades compartidas para los hooks de seguridad de Synapse.

Contexto y Propósito:
  Provee la infraestructura compartida para los hooks de seguridad de Claude Code:
  - Cliente del daemon de Laya (hooks/laya_daemon.py): el modelo vive en un proceso persistente y los hooks
    nunca importan torch (importarlo y cargar el checkpoint costaba ~7 s por hook).
  - Gestión de entrada y salida del protocolo PreToolUse (stdin JSON, stdout JSON 'allow'/'ask', stderr exit 2).
  - Alcance del proyecto (entorno git de CLAUDE_PROJECT_DIR) y resolución determinista de variables de shell.
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
  True  # si Laya está instalado y no se especificó --fallback
"""

from __future__ import annotations

import fcntl
import fnmatch
import functools
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import laya_daemon

# Solo se comprueba que existan: importarlos aquí costaba 1.7 s en cada hook
_LAYA_AVAILABLE = all(importlib.util.find_spec(m) is not None for m in ("laya", "torch"))

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
REGENERABLE_CACHE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}

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


def should_use_laya() -> bool:
    """Determina si se consulta a Laya System 1 o se opera solo con las reglas deterministas.

    Reglas:
      - Laya o torch no instalados: False (fallback determinista).
      - Flag --fallback: False.
      - En otro caso True: el daemon decide el dispositivo. Sin GPU (y sin --cpu) responde con error y el
        hook sigue solo con las reglas deterministas, igual que el fallback.
    """
    return _LAYA_AVAILABLE and "--fallback" not in sys.argv


def laya_mode() -> str:
    """'cpu' fuerza la inferencia en CPU (--cpu); 'gpu' exige CUDA. Cada modo tiene su propio daemon."""
    return "cpu" if "--cpu" in sys.argv else "gpu"


class LayaClient:
    """Misma firma que laya.Router.predict, resuelta por el daemon persistente (hooks/laya_daemon.py)."""

    def predict(self, state: Any, questions: dict[str, Any], model: str | None = None, delta: str | None = None) -> dict[str, Any]:
        request = {"op": "predict", "state": state, "questions": questions, "model": model, "delta": delta}
        return laya_daemon.request(request, laya_mode())["result"]


def get_laya_router() -> LayaClient:
    """Cliente del daemon de Laya. Lanza excepción si el daemon no responde: los hooks caen al fallback."""
    if not _LAYA_AVAILABLE:
        raise RuntimeError("Laya System 1 no está disponible en este entorno. Opere en modo fallback.")
    return LayaClient()


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
    return f"laya-daemon-{laya_mode()}" if should_use_laya() else "fallback"


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
        if step == "laya" and fields.get("result") not in {"skipped", "error"}:
            _TRACE["laya_consulted"] = True
        path = get_log_file(now)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            prune_old_logs(path.parent, now)  # una vez por día: al crear el archivo del día
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        # Un solo write() bajo flock: un hook matado a mitad de línea dejaba '{"ts"' pegado al registro siguiente
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:
        pass


LOG_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")


def prune_old_logs(log_dir: Path, now: datetime) -> None:
    """Borra las trazas diarias más antiguas que SYNAPSE_LOG_RETENTION_DAYS (30 por defecto; 0 = conservar todo)."""
    try:
        days = int(os.environ.get("SYNAPSE_LOG_RETENTION_DAYS", "30"))
    except ValueError:
        return
    if days <= 0:
        return
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    for path in log_dir.iterdir():
        match = LOG_NAME_RE.match(path.name)
        if match and match.group(1) < cutoff:
            path.unlink(missing_ok=True)


_RECORD_START = '{"ts"'


def parse_log_line(line: str) -> dict[str, Any] | None:
    """Registro de una línea de la traza; recupera el último registro completo si la línea quedó dañada."""
    line = line.replace("\x00", "").strip()
    start = line.rfind(_RECORD_START)
    for candidate in (line, line[start:] if start > 0 else ""):
        try:
            record = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            return record
    return None


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
    elif laya_mode() == "cpu":
        sys.stderr.write(
            "ℹ️ [INFO] Ejecutando Synapse (Laya System 1) en CPU.\n"
            "   Sugerencia: Para acelerar la inferencia a ~25ms, configure aceleración CUDA/GPU.\n"
        )


# "ALLOW" en llamadas directas de los hooks significa "sin opinión" (exit 0 sin JSON): Claude Code decide
_DECISION_NAMES = {"ALLOW": "pass", "PASSED": "pass", "ASK": "ask", "BLOCKED": "block", "allow": "allow", "ask": "ask"}


DECIDERS = ("python", "laya")


def record_audit_log(
    action: str,
    hook_name: str = "hook",
    tool_name: str = "",
    target: str = "",
    reason: str = "",
    *,
    decided_by: str | None = None,
    rule: str = "sin_objeciones",
    evidence: dict[str, Any] | None = None,
) -> None:
    """Registra el paso final 'decision': qué se decidió, quién (python | laya), con qué regla y por qué.

    Sin decided_by explícito: 'laya' si Laya fue consultado en esta ejecución (y no objetó), si no 'python'.
    """
    if not _TRACE:
        _TRACE.update(run=os.urandom(4).hex(), session="-", hook=hook_name, tool=tool_name, t0=datetime.now())
    decided_by = decided_by or ("laya" if _TRACE.get("laya_consulted") else "python")
    _TRACE["decided"] = True
    fields: dict[str, Any] = {"decision": _DECISION_NAMES.get(action, action.lower()), "decided_by": decided_by, "rule": rule, "target": target, "reason": reason}
    if evidence:
        fields["evidence"] = evidence
    log_step("decision", **fields)


def attribution(hook_name: str, decided_by: str, rule: str) -> str:
    """Encabezado visible en pantalla: qué hook decidió, con qué motor (python | laya) y con qué regla."""
    if decided_by not in DECIDERS:
        raise ValueError(f"decided_by debe ser uno de {DECIDERS}: {decided_by!r}")
    return f"Synapse · {hook_name} ({decided_by}) · regla {rule}"


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
    *,
    hook_name: str,
    tool_name: str,
    target: str,
    decided_by: str,
    rule: str,
    evidence: dict[str, Any] | None = None,
    context: str | None = None,
) -> None:
    """Emite 'allow' o 'ask' en formato PreToolUse. El motivo visible empieza con quién decidió y la regla.

    `context` llega al modelo como additionalContext: queda en la conversación para turnos siguientes.
    """
    shown = f"{attribution(hook_name, decided_by, rule)}: {reason}"
    record_audit_log(decision.lower(), hook_name, tool_name, target, reason, decided_by=decided_by, rule=rule, evidence=evidence)
    payload: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": shown,
        }
    }
    if context:
        payload["hookSpecificOutput"]["additionalContext"] = context
    sys.stdout.write(json.dumps(payload))
    sys.exit(0)


def emit_block(
    reason: str,
    *,
    hook_name: str,
    tool_name: str,
    target: str,
    decided_by: str,
    rule: str,
    evidence: dict[str, Any] | None = None,
    exit_code: int = 2,
) -> None:
    """Bloquea la herramienta (exit 2). stderr, que Claude Code muestra en pantalla, dice quién bloqueó y por qué."""
    header = attribution(hook_name, decided_by, rule)
    record_audit_log("BLOCKED", hook_name, tool_name, target, reason, decided_by=decided_by, rule=rule, evidence=evidence)
    sys.stderr.write(f"🚫 BLOCKED · {header}\n{reason}\n")
    sys.exit(exit_code)


def run_main(main: Any) -> None:
    """Ejecuta el main de un hook y garantiza que la ejecución cierre con un paso 'decision' en la traza.

    Las salidas tempranas (entrada vacía, otra herramienta) quedan como pass con regla 'exit.early'. Un error
    inesperado se registra (step 'error') y se relanza: Claude Code lo muestra como error no bloqueante.
    """
    try:
        main()
    except SystemExit:
        if _TRACE and not _TRACE.get("decided"):
            record_audit_log("ALLOW", _TRACE.get("hook", "hook"), _TRACE.get("tool", ""), rule="exit.early", reason="Sin nada que evaluar")
        raise
    except Exception as exc:
        log_step("error", error=f"{type(exc).__name__}: {exc}")
        if not _TRACE.get("decided"):
            record_audit_log("ALLOW", _TRACE.get("hook", "hook"), _TRACE.get("tool", ""), rule="hook.error", reason="Error interno del hook: Claude Code aplica sus permisos normales")
        raise
    if _TRACE and not _TRACE.get("decided"):
        record_audit_log("ALLOW", _TRACE.get("hook", "hook"), _TRACE.get("tool", ""), rule="exit.early", reason="Sin nada que evaluar")


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


def is_git_recoverable(path: str, cwd: str) -> bool:
    """Detecta un archivo o directorio que `git checkout HEAD -- <ruta>` restaura idéntico tras borrarlo.

    Exige contenido rastreado y commiteado sin ningún cambio local (modificado, solo en index, sin
    seguimiento o ignorado). Lo ignorado solo se tolera si es un artefacto regenerable (caches, build).
    Un directorio con `.git` propio (raíz del repo, repo anidado) nunca es recuperable: se perdería su historial.
    `skip-worktree`/`assume-unchanged` tampoco: ocultan a `git status` cambios locales que checkout no restaura.
    """
    resolved = resolve_path(path, cwd)
    if not os.path.lexists(resolved):
        return False
    is_dir = os.path.isdir(resolved) and not os.path.islink(resolved)
    if is_dir and os.path.lexists(os.path.join(resolved, ".git")):
        return False
    parent = resolved if is_dir else os.path.dirname(resolved)
    toplevel = _git_lines(parent, "rev-parse", "--show-toplevel")
    home = Path.home()
    if not toplevel or os.path.realpath(toplevel[0]) in (os.path.realpath(str(home / ".claude")), os.path.realpath(str(home))):
        return False
    tracked = _git_lines(parent, "ls-files", "-v", "--", resolved)
    if not tracked or any(line[0].islower() or line[0] == "S" for line in tracked):
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", parent, "status", "--porcelain", "-z", "--ignored", "--untracked-files=all", "--", resolved],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    for entry in filter(None, proc.stdout.split("\0")):
        p = Path(entry[3:])
        # Solo caches que el intérprete regenera; .bak/.log/build/ pueden ser trabajo manual
        regenerable = any(part in REGENERABLE_CACHE_DIRS for part in p.parts) or p.suffix.lower() in {".pyc", ".pyo"}
        if not (entry.startswith("!!") and regenerable):
            return False
    return True


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


_SEPARATORS = ("&&", "||", ";", "|")
_HEREDOC_START_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def _naive_split(command: str) -> list[str]:
    sep = re.compile(r"(\|\||&&|;|\||\n)")
    return [p.strip() for p in sep.split(command) if p and not sep.fullmatch(p) and p.strip()]


def extract_bash_tokens(command: str) -> list[str]:
    """Extrae sub-comandos separados por operadores de shell (&&, ||, ;, |, salto de línea).

    Respeta comillas: `sed 's/a|b/c/; s/x/y/'` es UN sub-comando (antes el `|` y el `;` entrecomillados lo
    partían y el fragmento no se podía analizar). Los cuerpos de heredoc se emiten línea a línea sin
    interpretar comillas, para que un apóstrofo en el cuerpo no oculte los comandos siguientes. Con una
    comilla sin cerrar se vuelve al corte ingenuo, que es el conservador.
    """
    command = re.sub(r"\\\n", " ", command)
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    pending: list[str] = []  # delimitadores de heredoc abiertos en la línea actual
    body_delims: list[str] = []

    def flush() -> None:
        if "".join(buf).strip():
            parts.append("".join(buf).strip())
        buf.clear()

    for line in command.split("\n"):
        if body_delims:
            if line.strip() == body_delims[0]:
                body_delims.pop(0)
            elif line.strip():
                parts.append(line.strip())
            continue
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and quote != "'" and i + 1 < len(line):
                buf.append(line[i:i + 2])
                i += 2
                continue
            if quote:
                quote = None if ch == quote else quote
                buf.append(ch)
                i += 1
                continue
            if ch in "'\"":
                quote = ch
                buf.append(ch)
                i += 1
                continue
            if line.startswith("<<<", i):
                buf.append("<<<")
                i += 3
                continue
            heredoc = _HEREDOC_START_RE.match(line, i)
            if heredoc:
                pending.append(heredoc.group(2))
                buf.append(heredoc.group(0))
                i = heredoc.end()
                continue
            sep = next((s for s in _SEPARATORS if line.startswith(s, i)), None)
            if sep:
                flush()
                i += len(sep)
                continue
            buf.append(ch)
            i += 1
        if quote:
            buf.append("\n")  # string multilínea (p. ej. mensaje de commit): sigue el mismo sub-comando
            continue
        flush()
        body_delims, pending = pending, []
    if quote:
        return _naive_split(command)
    flush()
    return parts


def parse_command_targets(subcmd: str) -> tuple[str, list[str]]:
    """Obtiene el nombre del comando base y sus argumentos de ruta."""
    try:
        tokens = shlex.split(subcmd, posix=True)
    except ValueError:
        return "", []
    if not tokens:
        return "", []
    tokens[0] = tokens[0].lstrip("(") or tokens[0]  # subshell: `(cd x && rm y)`
    # Palabras clave (`do rm x`, `then rm x`), asignaciones de entorno (VAR=x cmd) y envoltorios (sudo rm ...)
    # no son el comando real: sin esto, `for f in *; do rm -rf "$f"; done` no se analizaba
    while tokens and (re.match(r"^[A-Za-z_]\w*=", tokens[0]) or tokens[0] in COMMAND_WRAPPERS or tokens[0] in SHELL_KEYWORDS):
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
SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{"}
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


def _git_lines(cwd: str, *args: str) -> list[str]:
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=2)
    except Exception:
        return []
    return proc.stdout.splitlines() if proc.returncode == 0 else []


def allowed_roots(cwd: str) -> tuple[str, ...]:
    """Raíces donde Bash puede escribir: el entorno git del proyecto de la sesión.

    El ancla es CLAUDE_PROJECT_DIR (donde se abrió Claude Code), no el cwd: el agente puede hacer `cd` a
    cualquier parte y eso no debe ampliar el alcance. Sin la variable (tests, CLI) se usa el cwd.
    Del ancla se toman su toplevel git, el superproyecto (si es un submódulo) y todos los worktrees del repo,
    así una sesión en client/ puede trabajar en la raíz y una sesión en un worktree puede escribir en el
    repo principal. Un repo de dotfiles en ~ (o en /) nunca amplía el alcance: se vuelve al ancla.
    """
    return _roots_for(os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or cwd))


@functools.lru_cache(maxsize=8)
def _roots_for(anchor: str) -> tuple[str, ...]:
    candidates = _git_lines(anchor, "rev-parse", "--show-toplevel", "--show-superproject-working-tree")
    candidates += [line[len("worktree "):] for line in _git_lines(anchor, "worktree", "list", "--porcelain") if line.startswith("worktree ")]
    too_wide = {os.path.realpath(str(Path.home())), os.sep}
    roots = [r for r in dict.fromkeys(os.path.realpath(c) for c in candidates if c) if r not in too_wide]
    return tuple(roots) or (anchor,)


allowed_roots.cache_clear = _roots_for.cache_clear  # type: ignore[attr-defined]


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def is_outside_project(path: str, cwd: str, roots: str | tuple[str, ...] | list[str], follow_last: bool = True) -> bool:
    """True si la ruta resuelta cae fuera de todas las raíces. Excepciones: scratchpad de sesión y dispositivos.

    follow_last=False no sigue un symlink en el último componente: `rm link` borra el enlace, no su destino.
    Una ruta con variables sin resolver ($DIR/x) no es decidible aquí: se trata como interna.
    """
    resolved = resolve_path(path, cwd)
    if "$" in resolved:
        return False
    if SCRATCHPAD_RE.match(resolved) or resolved.startswith(HARMLESS_DEVICES):
        return False
    if follow_last:
        real = os.path.realpath(resolved)
    else:
        real = os.path.join(os.path.realpath(os.path.dirname(resolved)), os.path.basename(resolved))
    return not any(_within(real, root) for root in ([roots] if isinstance(roots, str) else roots))


# $NAME, ${NAME}, ${NAME:-defecto} / ${NAME-defecto}. Cualquier otra forma ($1, $?, ${x#y}) no se resuelve.
VAR_REF_RE = re.compile(r"\$\{([A-Za-z_]\w*)(?::?-([^}]*))?\}|\$([A-Za-z_]\w*)")
MAX_EXPANSIONS = 64
ShellVars = dict[str, "list[str] | None"]


def _lookup_var(name: str, variables: ShellVars, cwd: str | None) -> list[str] | None:
    if name in variables:
        return variables[name]  # None = asignada desde algo no resoluble ($(cmd), read)
    if name == "PWD":
        return [cwd] if cwd else None
    if name in os.environ:
        return [os.environ[name]]
    return None


def expand_word(word: str, variables: ShellVars, cwd: str | None) -> list[str] | None:
    """Valores posibles de una palabra de shell tras expandir variables; None si no es determinable.

    Las variables del propio comando (asignaciones, `for x in ...`) tienen prioridad sobre el entorno,
    como en bash. Sustituciones de comando ($(...), `...`) y parámetros especiales nunca se resuelven.
    """
    if "`" in word or "$(" in word:
        return None
    results = [""]
    pos = 0
    for m in VAR_REF_RE.finditer(word):
        literal = word[pos:m.start()]
        if "$" in literal:
            return None
        values = _lookup_var(m.group(1) or m.group(3), variables, cwd)
        if values is None and m.group(2) is not None:
            if "$" in m.group(2):
                return None
            values = [m.group(2)]
        if values is None:
            return None
        results = [r + literal + v for r in results for v in values]
        if len(results) > MAX_EXPANSIONS:
            return None
        pos = m.end()
    tail = word[pos:]
    if "$" in tail:
        return None
    return [r + tail for r in results]


GLOB_CHARS = set("*?[")


def _worst_case_glob(path: str) -> str:
    """Un segmento como '.*' puede expandirse a '..' en algunos shells: se evalúa como el directorio padre."""
    return "/".join(
        ".." if GLOB_CHARS & set(seg) and seg.startswith(".") and fnmatch.fnmatchcase("..", seg) else seg
        for seg in path.split("/")
    )


def resolve_candidates(word: str, variables: ShellVars, cwd: str | None) -> list[str] | None:
    """Rutas absolutas a las que puede apuntar una palabra; None si alguna no es determinable."""
    values = expand_word(word, variables, cwd)
    if values is None:
        return None
    resolved = []
    for value in values:
        value = _worst_case_glob(os.path.expanduser(value))
        if not os.path.isabs(value) and cwd is None:
            return None
        resolved.append(os.path.normpath(value if os.path.isabs(value) else os.path.join(cwd, value)))
    return resolved


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
        f"Lectura directa de archivo de credenciales: {file_path}",
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
