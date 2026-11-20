#!/usr/bin/env python3
"""PreToolUse hook para Bash — Bloqueo de comandos peligrosos potenciado por Laya System 1.

Reemplaza: /home/bypabloc/projects/bypabloc/optical-soft/.claude/hooks/block-dangerous.py

Contexto y Propósito:
  1. Auto-aprobación INCONDICIONAL para eliminación y manipulación de archivos temporales:
     - Archivos bajo ./tmp/** del proyecto.
     - Directorios efímeros de sesión (/tmp/claude-*).
     - Artefactos de compilación (dist, build, __pycache__, *.pyc, *.log, *.cache).
     - Archivos ignorados por Git (.gitignore).
  2. Bloqueo determinístico de comandos de impacto catastrófico inmediato (rm -rf /,
     fork bombs, mkfs, dd a dispositivos, git push force a main/master, curl | sh,
     exfiltración de credenciales, authorized_keys, shells reversas).
  3. Confirmación ('ask') determinística para operaciones difíciles de revertir (force push,
     reset --hard, DROP DATABASE, crontab -r...), evaluada ANTES del auto-allow.
  4. Laya System 1 solo escala a 'ask' (nunca bloquea) y recibe el comando sin comentarios
     ni cuerpos de heredoc, para que texto libre no pueda influir en su clasificación.
  5. Alcance determinista: toda escritura debe caer dentro del entorno git del proyecto (CLAUDE_PROJECT_DIR,
     su toplevel y sus worktrees). Las variables del comando y del entorno se resuelven antes de decidir
     (p=/home/x; rm -rf "$p" se bloquea); las no resolubles ($(cmd)) solo pierden el auto-allow.
  6. Confirmación determinística para cualquier comando que involucre la carpeta .git.
  7. Fallback automático a las reglas de seguridad determinísticas si no hay GPU o falla Laya.

Vínculos con la investigación en docs/research/laya/:
  - docs/research/laya/08-casos-de-uso-y-patrones-arquitecturales.md:
      Patrón "Guardrails de Seguridad y Pre-Gating para LLMs".
  - docs/research/laya/03-guia-de-uso-y-primitivas.md:
      Configuración de preguntas tipadas 'choice' con criterios semánticos neutrales.
  - docs/research/laya/10-tips-comunidad-antipatrones-y-limites-honestos.md:
      Uso de answer_confidence (AUROC 0.77) en lugar de act_probability y neutralización del binary bias.

Ejemplos de Uso en CLI:
  # 1. Caso auto-aprobado (archivo temporal):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "rm -f ./tmp/cache.log"}}' | python hooks/block_dangerous.py --gpu
  -> {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", ...}} [exit 0]

  # 2. Caso comando catastrófico (bloqueo):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}' | python hooks/block_dangerous.py --gpu
  -> 🚫 BLOCKED: Comando catalogado como catastrófico/destructivo [exit 2]

  # 3. Caso borrado permanente fuera de tmp (solicita confirmación al usuario):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "rm src/app.py"}}' | python hooks/block_dangerous.py --gpu
  -> {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", ...}} [exit 0]
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

# Agregar directorio actual a sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hosts  # noqa: E402

from datetime import datetime

from common import (
    HARMLESS_DEVICES,
    REDIRECT_GLUED_RE,
    REDIRECT_OP_RE,
    SCRATCHPAD_RE,
    TMP_SEGMENT_RE,
    ShellVars,
    allowed_roots,
    emit_block,
    emit_decision,
    expand_word,
    extract_bash_tokens,
    get_laya_router,
    is_disposable_target,
    is_git_recoverable,
    is_outside_project,
    is_system_tmp,
    laya_skip_reason,
    log_step,
    parse_command_targets,
    project_config_path,
    read_hook_input,
    record_audit_log,
    redirect_targets,
    resolve_candidates,
    run_main,
    should_use_laya,
)

# Pregunta binaria con claves neutras (A/B): las claves léxicas yes/no sesgan al checkpoint (issue #156).
# El delta afinado y la calibración de models/ se entrenaron con ESTA pregunta y con command_evidence():
# cambiar el texto invalida ambos (re-entrenar con scripts/laya_finetune.py y re-exportar con laya_eval.py).
DANGER_QUESTION: dict[str, dict[str, Any]] = {
    "danger": {
        "type": "choice",
        "instructions": "Should a human confirm this shell command before an AI coding agent runs it?",
        "criteria": {
            "A": "yes: it destroys data or the system, or compromises security (remote shell, privilege "
                 "escalation, credential theft, persistence, running downloaded code)",
            "B": "no: routine software development such as reading, searching, building, testing, "
                 "editing project files or committing",
        },
    }
}
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
LAYA_POLICY_FILE = MODELS_DIR / "laya-block-dangerous.json"
LAYA_MODEL = "english"  # el delta existe solo sobre este checkpoint: el router no debe elegir el multilingüe
LFS_POINTER_PREFIX = b"version https://git-lfs"

CATASTROPHIC_PATTERNS = [
    r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/(\s|\*|$)",
    r"rm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/(\s|\*|$)",
    r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+(~|\$HOME)(/|\s|\*|$)",
    r"rm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+(~|\$HOME)(/|\s|\*|$)",
    r":\(\)\{.*\}",  # fork bomb
    r"mkfs\.",
    r"dd\s+if=.*\s+of=/dev/",
    r">\s*/dev/sd[a-z]",
    r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+)?(777|0777)\s+/(\s|\*|$)",
    # [^;&|\n]*: el flag y la rama deben estar en el MISMO git push, no en sub-comandos posteriores
    r"git\s+push\s+[^;&|\n]*(--force|-f)\b[^;&|\n]*\b(origin\s+)?(main|master|dev)\b",
    r"git\s+push\s+[^;&|\n]*\b(origin\s+)?(main|master|dev)\b[^;&|\n]*(--force|-f)\b",
    r"git\s+push\s+--force-with-lease\s+[^;&|\n]*\b(origin\s+)?(main|master|dev)\b",
    r"git\s+branch\s+(-D|-d\s+--force|--delete\s+--force)\s+(main|master|dev)\b",
    r"git\s+clean\s+(-[a-zA-Z]*f[a-zA-Z]*d[a-zA-Z]*x?|-[a-zA-Z]*d[a-zA-Z]*f[a-zA-Z]*x?)\b",
    r"chmod\s+(-[a-zA-Z]+\s+)*0?777\s+/(etc|usr|bin|sbin|var|boot|lib|lib64|opt|root)\b",
    # Ejecución de scripts remotos
    r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b",
    # Exfiltración: credenciales o entorno enviados a la red
    r"(~|\$HOME|/home/[^/\s]+)/\.(ssh|aws|gnupg|kube|docker|config/gcloud)\b.*\b(curl|wget|nc|ncat|scp|rsync|ftp)\b",
    r"\b(env|printenv|set)\b\s*\|.*\b(curl|wget|nc|ncat)\b",
    # Matar todos los procesos del usuario: señal explícita (o --) y PID -1
    r"\bkill\s+(-s\s+\S+\s+|-\S+\s+|--\s+)+-1(\s|$|[;&|])",
    # Persistencia y shells reversas
    r">>?\s*\S*authorized_keys\b",
    r"\bn(c|cat)\b.*\s-(e|c)\s",
    r"/dev/(tcp|udp)/",
]

# Operaciones recuperables solo con esfuerzo: siempre requieren confirmación humana
ASK_PATTERNS = [
    r"git\s+push\b[^;&|\n]*(--force\b|--force-with-lease\b|\s-f\b)",
    r"git\s+filter-(branch|repo)\b",
    r"git\s+reset\s+--hard\b",
    r"(?i)\bdrop\s+(database|schema|table)\b",
    r"(?i)\btruncate\s+table\b",
    r"\bcrontab\s+-r\b",
    r"\baws\s+s3\s+(rb|rm)\b",
    r"\bsudo\s+rm\b",
    r"\bhistory\s+-c\b",
    # Contenedores con acceso root al host: privilegiado, raíz montada, PID del host o socket de Docker
    r"\bdocker\b.*\s--privileged\b",
    r"\bdocker\b.*\s(-v|--volume)[=\s]+/:",
    r"\bdocker\b.*\s--pid[=\s]+host\b",
    r"\bdocker\b.*docker\.sock\b",
    r"--unix-socket[=\s]+\S*docker\.sock\b",  # API de Docker directa (las consultas GET se eximen)
    # Servidor HTTP que expone el home o la raíz del sistema a la red
    r"\bhttp\.server\b.*\s(-d|--directory)[=\s]+(~|\$HOME|/home/[^/\s]+|/root|/)/?(\s|$)",
]

# Sub-comandos que solo buscan o listan: `rg "DROP DATABASE" devtools` o `test -S /var/run/docker.sock` no
# ejecutan nada de lo que mencionan. echo/printf/cat NO están: su salida puede ir a psql por un pipe.
ASK_EXEMPT_CMDS = {
    "rg", "grep", "egrep", "fgrep", "ag", "ls", "eza", "exa", "tree", "test", "[", "[[", "stat", "file",
    "which", "type", "du", "readlink", "realpath", "jq", "yq",
}
DOCKER_API_READ_RE = re.compile(r"^(https?://[^/\s]*)?/(v[\d.]+/)?(_ping|info|version|containers/json|images/json|system/df)\b")
CURL_WRITE_FLAGS_RE = re.compile(r"^(-X|--request|-d|--data.*|-F|--form|-T|--upload-file|--json)(=|$)|^-[a-zA-Z]*[XdFT]")


def is_docker_api_read(subcmd: str) -> bool:
    """curl --unix-socket docker.sock con GET a un endpoint de consulta (/_ping, /info, /version, /containers/json)."""
    cmd, _ = parse_command_targets(subcmd)
    if cmd != "curl" or "docker.sock" not in subcmd:
        return False
    try:
        tokens = shlex.split(subcmd, posix=True)
    except ValueError:
        return False
    if any(CURL_WRITE_FLAGS_RE.match(t) for t in tokens):
        return False
    urls = [t for t in tokens if t.startswith(("http://", "https://"))]
    return bool(urls) and all(DOCKER_API_READ_RE.match(u) for u in urls)


def ask_pattern_hit(command: str) -> str | None:
    """Primer patrón de ASK_PATTERNS presente en algún sub-comando que no sea de búsqueda o listado."""
    for sc in extract_bash_tokens(command):
        cmd, _ = parse_command_targets(sc)
        if cmd in ASK_EXEMPT_CMDS or is_docker_api_read(sc):
            continue
        for pat in ASK_PATTERNS:
            if re.search(pat, sc):
                return pat
    return None


DESTRUCTIVE_CMDS = {"rm", "unlink", "rmdir", "shred"}

# Comandos de archivos que escriben o destruyen sus argumentos (todos) o solo su destino (último)
MUTATING_CMDS = {"rm", "unlink", "rmdir", "shred", "mv", "truncate", "chmod", "chown", "tee", "touch"}
INPLACE_EDITORS = {"sed", "perl"}  # solo con -i
DEST_ONLY_CMDS = {"cp", "rsync", "install", "ln"}


INPLACE_PROGRAM_FLAG_RE = re.compile(r"^(-[a-zA-Z]*[ef]|--(expression|file))$")


def _inplace_files(subcmd: str, paths: list[str]) -> list[str]:
    """Archivos que edita `sed -i`/`perl -i`, sin el script: `sed -i '25s/a/b/' f` solo edita f.

    El script es el primer posicional o el valor de -e/-f; no es una ruta que se escriba.
    """
    try:
        tokens = shlex.split(subcmd, posix=True)
    except ValueError:
        return paths
    programs = [tokens[i + 1] for i, t in enumerate(tokens[:-1]) if INPLACE_PROGRAM_FLAG_RE.match(t)]
    if not programs:
        return paths[1:]
    files = list(paths)
    for prog in programs:
        if prog in files:
            files.remove(prog)
    return files


def write_targets(subcmd: str) -> list[str]:
    """Rutas que un sub-comando modifica: redirecciones de salida + argumentos de comandos de escritura."""
    targets = redirect_targets(subcmd)
    cmd, paths = parse_command_targets(subcmd)
    if cmd in MUTATING_CMDS:
        targets += paths
    elif cmd in INPLACE_EDITORS and re.search(r"\s-[a-zA-Z]*i", subcmd):
        targets += _inplace_files(subcmd, paths)
    elif cmd in DEST_ONLY_CMDS and paths:
        targets.append(paths[-1])
    elif cmd == "dd":
        targets += [p[3:] for p in paths if p.startswith("of=")]
    elif cmd == "find" and paths and re.search(r"\s-(delete|exec\s+(rm|shred|mv|truncate))\b", subcmd):
        targets.append(paths[0])
    elif cmd == "git":
        targets += git_write_targets(subcmd)
    return targets


# Subcomandos de git que escriben en el repo donde corren (spec 001 · R-04)
GIT_WRITING_SUBCMDS = {
    "config", "remote", "commit", "checkout", "switch", "reset", "merge", "rebase", "pull", "fetch", "worktree",
    "init", "apply", "am", "stash", "add", "rm", "mv", "restore", "clean", "tag", "branch", "cherry-pick", "revert",
    "submodule", "gc", "prune", "update-ref", "update-index", "notes",
}


def git_write_targets(subcmd: str) -> list[str]:
    """Directorios que un git escribe: destino de clone/init y -C / --git-dir / --work-tree con un subcomando que
    escribe. Brecha del 2026-11-20: `git clone url ../x` y `git -C ../x remote set-url` no se bloqueaban."""
    try:
        tokens = shlex.split(subcmd)
    except ValueError:
        return []
    if not tokens or os.path.basename(tokens[0]) != "git":
        return []
    dirs: list[str] = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-C" and i + 1 < len(tokens):
            dirs.append(tokens[i + 1])
            i += 2
        elif tok in {"-c", "--namespace", "--exec-path"} and i + 1 < len(tokens):
            i += 2
        elif tok.startswith(("--git-dir=", "--work-tree=")):
            dirs.append(tok.split("=", 1)[1])
            i += 1
        elif tok in {"--git-dir", "--work-tree"} and i + 1 < len(tokens):
            dirs.append(tokens[i + 1])
            i += 2
        elif tok.startswith("-"):
            i += 1
        else:
            break
    if i >= len(tokens):
        return []
    sub, rest = tokens[i], [t for t in tokens[i + 1 :] if not t.startswith("-")]
    targets = dirs if sub in GIT_WRITING_SUBCMDS else []
    if sub == "clone" and len(rest) >= 2:
        targets.append(rest[1])
    elif sub == "init" and rest:
        targets.append(rest[0])
    return targets


# ---------------------------------------------------------------- reglas multi-agente (spec 001 · R-01, R-05, T-28)

# El usuario aprueba los 'ask' de Codex con scripts/synapse.py approve <id>; el agente nunca (R-01)
SELF_APPROVAL_RE = re.compile(r"synapse(\.py)?[\"']?\s+approve\b|[/\\]approvals[/\\](pending|approved|consumed)\b")
# Otro agente lanzado sin los guardrails del host actual (R-05)
AGENT_WRAPPERS = {
    "codex": {"exec", "e"},
    "claude": {"-p", "--print"},
    "opencode": {"run"},
    "agy": {"-p", "--print", "--prompt"},
    "gemini": {"-p", "--prompt"},
}
UNSAFE_AGENT_FLAGS_RE = re.compile(r"--dangerously-[\w-]+|--yolo\b|--full-auto\b")
# Windows (PowerShell / cmd): mínimo determinista; la cobertura completa es el spec 002 (T-28)
WINDOWS_DISK_RE = re.compile(r"(?i)\b(Format-Volume|Clear-Disk|diskpart)\b|(^|[;&|]\s*)format\s+[a-z]:")
WINDOWS_REMOTE_FETCH_RE = re.compile(r"(?i)\b(iwr|irm|Invoke-WebRequest|Invoke-RestMethod|DownloadString|DownloadFile)\b")
WINDOWS_EVAL_RE = re.compile(r"(?i)\b(iex|Invoke-Expression)\b")
WINDOWS_DESTRUCTIVE_RE = re.compile(
    r"(?i)\b(Remove-Item|ri)\b[^;|&\n]*?\s-(Recurse|Force)\b|(^|[;&|]\s*)(rd|rmdir|del|erase)\s+[^;|&\n]*?/[sq]\b"
)


def agent_wrapper(command: str) -> tuple[str, str] | None:
    """('block' | 'ask', detalle) si el comando lanza otro agente en modo no interactivo."""
    for sc in extract_bash_tokens(command):
        try:
            tokens = shlex.split(sc)
        except ValueError:
            continue
        if not tokens:
            continue
        name = os.path.basename(tokens[0]).removesuffix(".exe").removesuffix(".cmd")
        modes = AGENT_WRAPPERS.get(name)
        if modes and any(t in modes for t in tokens[1:3]):
            if UNSAFE_AGENT_FLAGS_RE.search(sc):
                return "block", f"'{name}' sin sus protecciones ({UNSAFE_AGENT_FLAGS_RE.search(sc).group(0)})"
            return "ask", f"'{name}' en modo no interactivo: ese agente corre sin los guardrails de esta sesión"
    return None


def windows_rule(command: str) -> tuple[str, str, str] | None:
    """(verdict, regla, motivo) para patrones de PowerShell/cmd peligrosos."""
    if WINDOWS_DISK_RE.search(command):
        return "block", "windows.disk", "Operación de disco destructiva en Windows (formatear o limpiar un volumen)."
    if WINDOWS_EVAL_RE.search(command) and WINDOWS_REMOTE_FETCH_RE.search(command):
        return "block", "windows.remote_exec", "Ejecución de un script descargado (iex con Invoke-WebRequest/irm)."
    match = WINDOWS_DESTRUCTIVE_RE.search(command)
    if match and not TMP_SEGMENT_RE.search(match.group(0).replace("\\", "/")):
        return "ask", "windows.destructive", "Borrado recursivo o forzado en Windows fuera de ./tmp/: se requiere confirmación explícita."
    return None


def touches_git_dir(path: str) -> bool:
    """.git/ guarda todo el historial: cualquier comando de archivos que la toque pide confirmación."""
    return ".git" in os.path.normpath(path).split(os.sep)


# Flags cuyo valor es un patrón de exclusión o búsqueda, no una ruta que se lea o escriba (rg --glob '!.git')
PATTERN_FLAGS = {
    "--exclude", "--exclude-dir", "--ignore", "--ignore-glob", "--ignore-dir", "-E", "-I", "-g", "--glob",
    "-name", "-iname", "-path", "-ipath", "-not", "!", "--filter", "-x",
}


# Comandos cuyo primer argumento posicional es un programa o patrón, no una ruta (jq '.git', rg '.git' src).
# Con -e/-f el programa va en el flag y el primer posicional ya es un archivo.
PROGRAM_FIRST_CMDS = {"jq", "yq", "gojq", "grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "gawk", "mawk"}
PROGRAM_FLAG_RE = re.compile(r"^(-[ef]|--(regexp|file|expression|from-file))(=|$)")


def _program_arg_index(tokens: list[str], subcmd: str) -> int | None:
    cmd, _ = parse_command_targets(subcmd)
    if cmd not in PROGRAM_FIRST_CMDS or any(PROGRAM_FLAG_RE.match(t) for t in tokens):
        return None
    start = next((i for i, t in enumerate(tokens) if os.path.basename(t) == cmd), None)
    if start is None:
        return None
    return next((i for i in range(start + 1, len(tokens)) if not tokens[i].startswith("-")), None)


def git_dir_refs(subcmd: str, variables: ShellVars, cwd: str | None) -> list[str]:
    """Argumentos del sub-comando que apuntan a una carpeta .git (también vía variables: d=.git; rm -rf $d)."""
    try:
        tokens = shlex.split(subcmd, posix=True)
    except ValueError:
        return []
    program = _program_arg_index(tokens, subcmd)
    refs: list[str] = []
    prev = ""
    for i, tok in enumerate(tokens):
        if i != program and prev not in PATTERN_FLAGS and not tok.startswith("-"):
            values = expand_word(tok, variables, cwd) if "$" in tok else [tok]
            if any(touches_git_dir(v) for v in values or []):
                refs.append(tok)
        prev = tok
    return refs


DECLARE_CMDS = {"export", "local", "declare", "readonly", "typeset"}
ASSIGN_RE = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.S)


def track_variables(subcmd: str, variables: ShellVars, cwd: str | None) -> bool:
    """Registra asignaciones, `for x in ...`, `read x` y `unset x`. True si el sub-comando era solo eso.

    Una asignación desde algo no determinable (p=$(mktemp -d)) queda como None: sus usos no se resuelven y
    pierden el auto-allow, pero no se bloquean.
    """
    try:
        tokens = shlex.split(subcmd, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    head = tokens[0]
    if head == "for":
        if len(tokens) >= 3 and tokens[2] == "in":
            values: list[str] | None = []
            for word in tokens[3:]:
                expanded = expand_word(word, variables, cwd)
                if expanded is None:
                    values = None
                    break
                values += expanded
            variables[tokens[1]] = values
        elif len(tokens) >= 2:
            variables[tokens[1]] = None  # `for x; do` itera los argumentos posicionales
        return True
    if head == "unset":
        for name in tokens[1:]:
            variables.pop(name, None)
        return True
    if head == "read":
        for name in tokens[1:]:
            if not name.startswith("-"):
                variables[name] = None
        return True
    args = [t for t in tokens[1:] if not t.startswith("-")] if head in DECLARE_CMDS else tokens
    if not all(ASSIGN_RE.match(t) or (head in DECLARE_CMDS and re.fullmatch(r"[A-Za-z_]\w*", t)) for t in args):
        return False
    for tok in args:
        match = ASSIGN_RE.match(tok)
        if match:
            variables[match.group(1)] = expand_word(match.group(2), variables, cwd)
    return True


# Comandos rutinarios que no pasan por Laya (ya superaron las regex de bloqueo y ask):
# un 'ask' falso en estos detiene loops autónomos (p. ej. gh pr create bloqueado 3 veces seguidas)
ROUTINE_RE = re.compile(
    r"^\s*(GH_TOKEN=(\$\(gh\s+auth\s+token[^)]*\)|[\w.-]+)\s+)?("
    r"gh\s+(pr|run|issue|repo\s+view|api\s+repos/\S+/(pulls|issues|actions))\b"
    r"|git\s+(status|add|commit|fetch|pull|push|log|diff|show|switch|branch|stash\s+(list|push|save)|tag)\b"
    r")"
)


def is_routine(command: str) -> bool:
    subcmds = extract_bash_tokens(command)
    return bool(subcmds) and all(ROUTINE_RE.match(sc) for sc in subcmds)


# Comandos que solo leen o imprimen: un pipeline formado solo por ellos no tiene nada que preguntarle a Laya
READ_ONLY_CMDS = {
    "ls", "eza", "exa", "tree", "cat", "bat", "batcat", "head", "tail", "wc", "sort", "uniq",
    "cut", "tr", "nl", "column", "diff", "cmp", "comm", "file", "stat", "du", "df", "pwd", "echo", "printf",
    "true", "false", "test", "[", "[[", "]", "date", "which", "type", "whereis", "realpath", "readlink",
    "basename", "dirname", "grep", "egrep", "fgrep", "rg", "ag", "fd", "fdfind", "jq", "yq", "md5sum",
    "sha1sum", "sha256sum", "hexdump", "xxd", "od", "strings", "sleep", "cd", "seq", "id", "whoami", "uname",
    "hostname", "nproc", "free", "uptime", "ps", "pgrep", "fi", "done", "esac",
}  # sin less/more (pueden abrir una shell) ni set (set +o history desactiva el historial)
GIT_READ_SUBCMDS = {
    "status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "blame", "grep", "describe",
    "shortlog", "cat-file", "merge-base", "name-rev", "check-ignore", "rev-list", "whatchanged",
}
FIND_ACTIONS_RE = re.compile(r"\s-(delete|exec|execdir|ok|okdir|fprint|fprintf|fls)\b")
SED_WRITE_RE = re.compile(r"(^|[;{}\s])[wWe]\s|/[gpIiMm0-9]*[we](\s|$|;)")
AWK_SIDE_EFFECT_RE = re.compile(r"system\s*\(|[|>]|getline")
SUBSHELL_RE = re.compile(r"\$\(|`|<\(|>\(")


def _git_subcommand(tokens: list[str]) -> str:
    rest = tokens[1:]
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in {"-C", "-c"} else rest[1:]
    return rest[0] if rest else ""


def _reads_inside(tokens: list[str], cwd: str | None, roots: tuple[str, ...]) -> bool:
    """Toda ruta absoluta, con ~ o con .. debe caer dentro del proyecto: leer /etc, /tmp o ~ lo evalúa Laya."""
    for tok in tokens:
        value = tok.split("=", 1)[1] if tok.startswith("--") and "=" in tok else tok
        if not value.startswith(("/", "~", "$HOME", "${HOME}")) and ".." not in value.split("/"):
            continue
        if value.startswith(HARMLESS_DEVICES):
            continue
        if not roots or cwd is None:
            return False
        candidates = resolve_candidates(value, {}, cwd)
        if not candidates or any(is_outside_project(c, "/", roots) for c in candidates):
            return False
    return True


def is_read_only(command: str, cwd: str | None = None, roots: tuple[str, ...] = ()) -> bool:
    """True si todos los sub-comandos solo leen dentro del proyecto: la capa determinista basta y Laya no corre.

    Excluye todo lo que pueda ejecutar código o escribir: sustituciones $(...), heredocs, intérpretes,
    binarios invocados por ruta, `find -exec`, `sed` con w/e, `awk` con system()/redirecciones, lecturas
    de secretos (~/.ssh...), lecturas fuera del proyecto y redirecciones fuera de un tmp/ (las de tmp/ ya
    pasaron el control de alcance). Sin cwd/roots, cualquier ruta absoluta cuenta como externa.
    """
    if SUBSHELL_RE.search(command) or split_heredocs(command)[1] or SECRET_PATH_RE.search(command):
        return False
    eff_cwd = cwd
    for sc in extract_bash_tokens(command):
        if ASSIGN_RE.match(sc) and track_variables(sc, {}, None):
            continue
        targets = [t for t in redirect_targets(sc) if not t.startswith(HARMLESS_DEVICES)]
        if any(not TMP_SEGMENT_RE.search(t) for t in targets):
            return False
        try:
            tokens = shlex.split(sc, posix=True)
        except ValueError:
            return False
        cmd, _ = parse_command_targets(sc)
        if not cmd:
            continue
        program = next((t for t in tokens if os.path.basename(t) == cmd), cmd)
        if "/" in program or not _reads_inside(tokens[1:], eff_cwd, roots):
            return False
        if cmd == "cd":
            dest = resolve_candidates(tokens[1] if len(tokens) > 1 else "~", {}, eff_cwd)
            eff_cwd = dest[0] if dest and len(dest) == 1 else None
            continue
        if cmd == "git":
            if _git_subcommand(tokens) not in GIT_READ_SUBCMDS:
                return False
        elif cmd == "sed":
            if INPLACE_FLAG_RE.search(sc) or SED_WRITE_RE.search(" ".join(tokens[1:])):
                return False
        elif cmd == "find":
            if FIND_ACTIONS_RE.search(sc):
                return False
        elif cmd in {"awk", "gawk", "mawk"}:
            if AWK_SIDE_EFFECT_RE.search(" ".join(t for t in tokens[1:] if not t.startswith("-"))):
                return False
        elif cmd not in READ_ONLY_CMDS:
            return False
    return True


# Gestores de paquetes que solo reconcilian el proyecto con su propio lockfile: sin nombres de paquete,
# sin instalación global y sin rutas fuera del proyecto. `uv add`, `bun install pkg` o `uv run` siguen a Laya.
LOCKFILE_SUBCMDS = {"uv": {"lock", "sync"}, "bun": {"install"}, "pnpm": {"install"}}
LOCKFILE_VALUE_FLAGS = {
    "--directory", "--project", "--python", "-p", "--cwd", "-C", "--dir", "--cache-dir", "--config-file",
    "--extra", "--group", "--only-group", "--no-group", "--package",
}
GLOBAL_INSTALL_FLAGS = {"-g", "--global"}


def _is_lockfile_subcommand(tokens: list[str], cwd: str | None, roots: tuple[str, ...]) -> bool:
    allowed = LOCKFILE_SUBCMDS.get(tokens[0]) if tokens else None
    if not allowed or GLOBAL_INSTALL_FLAGS.intersection(tokens):
        return False
    positionals: list[str] = []
    rest = iter(tokens[1:])
    for tok in rest:
        if tok in LOCKFILE_VALUE_FLAGS:
            next(rest, None)
        elif not tok.startswith("-"):
            positionals.append(tok)
    return len(positionals) == 1 and positionals[0] in allowed and _reads_inside(tokens[1:], cwd, roots)


def _without_redirects(tokens: list[str]) -> list[str]:
    kept: list[str] = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
        elif REDIRECT_OP_RE.fullmatch(tok):
            skip_next = True
        elif not REDIRECT_GLUED_RE.match(tok):
            kept.append(tok)
    return kept


def is_lockfile_sync(command: str, cwd: str | None = None, roots: tuple[str, ...] = ()) -> bool:
    """True si el comando regenera o sincroniza el lockfile del proyecto (uv lock|sync, bun|pnpm install).

    Los demás sub-comandos deben ser de solo lectura (cd, tail...). Escribir el lockfile es su trabajo
    propio, igual que un build: Laya puntuaba solo las palabras y pedía confirmación sin motivo.
    """
    if SUBSHELL_RE.search(command) or split_heredocs(command)[1] or SECRET_PATH_RE.search(command):
        return False
    eff_cwd = cwd
    found = False
    for sc in extract_bash_tokens(command):
        try:
            tokens = shlex.split(sc, posix=True)
        except ValueError:
            return False
        targets = [t for t in redirect_targets(sc) if not t.startswith(HARMLESS_DEVICES)]
        if tokens and tokens[0] in LOCKFILE_SUBCMDS and not any(not TMP_SEGMENT_RE.search(t) for t in targets):
            if not _is_lockfile_subcommand(_without_redirects(tokens), eff_cwd, roots):
                return False
            found = True
            continue
        if not is_read_only(sc, eff_cwd, roots):
            return False
        if tokens and tokens[0] == "cd":
            dest = resolve_candidates(tokens[1] if len(tokens) > 1 else "~", {}, eff_cwd)
            eff_cwd = dest[0] if dest and len(dest) == 1 else None
    return found


# ---------------------------------------------------------------- comandos de desarrollo rutinarios
# Aprendidos de la traza real (klinikae, oct-nov 2026): ~20 mil consultas a Laya, 24 'ask' y los 24 falsos
# positivos (bun run typecheck, git worktree list, pdfinfo, timeout N tail -f /dev/null...). Si un comando
# solo combina estos sub-comandos con lecturas, Laya no aporta: se decide aquí ('pass', nunca 'allow').

PROJECT_COMMANDS_CONFIG = os.path.join(".claude", "synapse-commands.json")
PACKAGE_RUNNERS = {"bun", "npm", "pnpm", "yarn"}
RUNNER_VALUE_FLAGS = {"--cwd", "-C", "--prefix", "--dir", "--filter", "-F", "--workspace", "-w"}
UV_VALUE_FLAGS = {"--directory", "--project", "--package", "-p", "--python", "--group", "--extra", "--env-file"}
DEV_TOOLS = {
    "pytest", "mypy", "ruff", "black", "isort", "pyright", "coverage", "tsc", "eslint", "prettier", "vitest",
    "jest", "biome", "pdfinfo",
}
SCRIPT_RUNNERS = {"bun", "node", "python", "python3", "deno", "tsx"}
GIT_ROUTINE_SUBCMDS = GIT_READ_SUBCMDS | {
    "add", "commit", "fetch", "pull", "push", "switch", "branch", "tag", "stash", "worktree", "merge-base",
}
GIT_WORKTREE_ROUTINE = {"list", "add", "prune"}
# None = cualquier acción salvo las excluidas; gh api/auth y gh repo delete quedan fuera
GH_ROUTINE: dict[str, set[str] | None] = {"pr": None, "run": None, "issue": None, "repo": {"view"}, "release": {"view", "list"}}
GH_EXCLUDED = {("pr", "merge")}
GRAPHIFY_ROUTINE = {"query", "explain", "path", "update"}


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Quita asignaciones (VAR=x), `timeout N`, `time` y `nice` del inicio del sub-comando."""
    while tokens:
        if ASSIGN_RE.match(tokens[0]):
            tokens = tokens[1:]
        elif tokens[0] == "timeout":
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
            if tokens and re.fullmatch(r"\d+(\.\d+)?[smhd]?", tokens[0]):
                tokens = tokens[1:]
        elif tokens[0] in {"time", "nice"}:
            tokens = tokens[1:]
        else:
            break
    return tokens


def _skip_flags(tokens: list[str], value_flags: set[str]) -> list[str]:
    """Saltea flags (y sus valores) hasta el primer posicional."""
    rest = list(tokens)
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in value_flags else rest[1:]
    return rest


def _is_tracked_script(path: str, cwd: str | None, roots: tuple[str, ...]) -> bool:
    """Script versionado del proyecto (git ls-files): su contenido se revisa en el repo, no en el comando."""
    if not cwd or not roots or not re.search(r"\.(ts|js|mjs|cjs|py|tsx)$", path):
        return False
    resolved = os.path.normpath(os.path.join(cwd, path))
    if is_outside_project(resolved, "/", roots) or TMP_SEGMENT_RE.search(os.path.relpath(resolved, cwd)):
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", os.path.dirname(resolved), "ls-files", "--error-unmatch", os.path.basename(resolved)],
            capture_output=True, timeout=2,
        )
    except Exception:
        return False
    return proc.returncode == 0


def project_routine_prefixes(cwd: str | None) -> list[list[str]]:
    """`routine_commands` de $CLAUDE_PROJECT_DIR (o cwd)/.claude/synapse-commands.json, como listas de tokens.

    Ejemplo: {"routine_commands": ["bun devtools/run.ts", "graphify"]}. Archivo ausente o inválido -> [].
    """
    path = project_config_path("commands", cwd or "")
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            values = json.load(fh).get("routine_commands")
    except (OSError, ValueError, AttributeError):
        return []
    prefixes: list[list[str]] = []
    for value in values if isinstance(values, list) else []:
        try:
            tokens = shlex.split(value) if isinstance(value, str) else []
        except ValueError:
            continue
        if tokens:
            prefixes.append(tokens)
    return prefixes


def is_dev_subcommand(tokens: list[str], cwd: str | None, roots: tuple[str, ...], prefixes: list[list[str]]) -> bool:
    """Un sub-comando de desarrollo rutinario (tokens ya sin redirecciones)."""
    tokens = _strip_wrappers(tokens)
    if not tokens:
        return False
    if any(tokens[: len(p)] == p for p in prefixes):
        return True
    head, rest = tokens[0], tokens[1:]
    if head in DEV_TOOLS:
        return True
    if head in PACKAGE_RUNNERS:
        positional = _skip_flags(rest, RUNNER_VALUE_FLAGS)
        if positional and positional[0] in {"run", "test", "typecheck", "lint"}:
            return True
    if head == "uv" and rest[:1] == ["run"]:
        positional = _skip_flags(rest[1:], UV_VALUE_FLAGS)
        if "--with" in rest or not positional:
            return False
        if positional[0] in DEV_TOOLS:
            return True
        if positional[0] in {"python", "python3"} and positional[1:2] == ["-m"] and positional[2:3] and positional[2] in DEV_TOOLS:
            return True
        return bool(positional[0] in SCRIPT_RUNNERS and positional[1:] and _is_tracked_script(positional[1], cwd, roots))
    if head in {"python", "python3"} and rest[:1] == ["-m"] and rest[1:2] and rest[1] in DEV_TOOLS:
        return True
    if head in LOCKFILE_SUBCMDS and _is_lockfile_subcommand(tokens, cwd, roots):
        return True
    if head in SCRIPT_RUNNERS:
        positional = _skip_flags(rest, set())
        return bool(positional) and _is_tracked_script(positional[0], cwd, roots)
    if head == "git":
        sub = _git_subcommand(tokens)
        if sub == "worktree":
            idx = tokens.index("worktree")
            return tokens[idx + 1: idx + 2] != [] and tokens[idx + 1] in GIT_WORKTREE_ROUTINE
        return sub in GIT_ROUTINE_SUBCMDS
    if head == "gh":
        if not rest or rest[0] not in GH_ROUTINE:
            return False
        actions = GH_ROUTINE[rest[0]]
        action = rest[1] if len(rest) > 1 else ""
        return (rest[0], action) not in GH_EXCLUDED and (actions is None or action in actions)
    if head == "graphify":
        return bool(rest) and rest[0] in GRAPHIFY_ROUTINE
    if head == "mkdir":
        return _reads_inside(rest, cwd, roots)
    return False


def is_dev_routine(command: str, cwd: str | None = None, roots: tuple[str, ...] = ()) -> bool:
    """True si cada sub-comando es de desarrollo rutinario o de solo lectura (con `cd` dentro del proyecto).

    Excluye sustituciones $(...), heredocs y lecturas de secretos (los ve Laya, si está encendida).
    Las redirecciones deben ir a ./tmp/, al scratchpad, a un *.log o a /dev/null.
    """
    if SUBSHELL_RE.search(command) or split_heredocs(command)[1] or SECRET_PATH_RE.search(command):
        return False
    prefixes = project_routine_prefixes(cwd)
    eff_cwd = cwd
    variables: ShellVars = {}
    found = False
    for sc in extract_bash_tokens(command):
        if track_variables(sc, variables, eff_cwd):
            continue
        targets = [t for t in redirect_targets(sc) if not t.startswith(HARMLESS_DEVICES)]
        if not all(_is_disposable_write(t) for t in targets):
            return False
        try:
            tokens = _without_redirects(shlex.split(sc, posix=True))
        except ValueError:
            return False
        if tokens and tokens[0].startswith("("):
            tokens[0] = tokens[0].lstrip("(")
        tokens = [t for t in tokens if t not in {")", "("}]
        if tokens and tokens[-1].endswith(")"):
            tokens[-1] = tokens[-1].rstrip(")")
        if tokens and tokens[0] == "cd":
            dest = resolve_candidates(tokens[1] if len(tokens) > 1 else "~", variables, eff_cwd)
            eff_cwd = dest[0] if dest and len(dest) == 1 else None
            if eff_cwd is None or is_outside_project(eff_cwd, "/", roots):
                return False
            continue
        if is_dev_subcommand(tokens, eff_cwd, roots, prefixes):
            found = True
            continue
        if not is_read_only(sc, eff_cwd, roots):
            return False
    return found


HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
SHELL_OP_RE = re.compile(r"[();<>|&]+")
NEEDS_QUOTE_RE = re.compile(r"[\s();<>|&'\"]")


def split_heredocs(command: str, keep_terminators: bool = False) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Separa las líneas de shell de los cuerpos de heredoc: (líneas, [(línea dueña, cuerpo)]).

    keep_terminators conserva la línea de cierre (EOF) para que extract_bash_tokens vea el heredoc cerrado.
    """
    kept: list[str] = []
    bodies: list[tuple[str, list[str]]] = []
    delimiter: str | None = None
    for line in command.splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
                if keep_terminators:
                    kept.append(line)
            else:
                bodies[-1][1].append(line)
            continue
        match = HEREDOC_RE.search(line)
        if match:
            delimiter = match.group(2)
            bodies.append((line, []))
        kept.append(line)
    return kept, bodies


def strip_for_classifier(command: str) -> str:
    """Quita comentarios y cuerpos de heredoc antes de Laya, conservando las comillas de los argumentos.

    Ambos son texto libre que el agente (o una inyección) controla: medido en 45 comandos, un comentario
    cambia la decisión de Laya en ~15% de los casos y puede volver inofensivo uno malicioso.
    Sin comillas, `rg "a|b"` llega como `rg a|b` (un pipe falso) y Laya lo marcaba catastrófico.
    """
    kept, _ = split_heredocs(command)
    try:
        lexer = shlex.shlex("\n".join(kept), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return " ".join(kept)
    return " ".join(t if SHELL_OP_RE.fullmatch(t) or not NEEDS_QUOTE_RE.search(t) else shlex.quote(t) for t in tokens)


INTERPRETER_RE = re.compile(r"^(python|node|bun|deno|ruby|perl|php|bash|sh|zsh)[\d.]*$")
NETWORK_CMDS = {"curl", "wget", "nc", "ncat", "ssh", "scp", "rsync", "ftp", "telnet", "socat"}
REMOTE_EXEC_RE = re.compile(r"\b(curl|wget)\b[^\n;&]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b")
SECRET_PATH_RE = re.compile(
    r"(~|\$HOME|/home/[^/\s]+|/root)/\.(ssh|aws|gnupg|kube|docker|netrc|config/gcloud)[^\s'\"|;&]*"
    r"|/etc/(shadow|sudoers|gshadow)\b"
)
SCRIPT_EFFECTS = (
    ("writes files", re.compile(r"open\([^)]*['\"][wax]|write_text|write_bytes|writeFile|\.write\(")),
    ("deletes files", re.compile(r"\brm\s|unlink|rmtree|os\.remove|rmdir|rmSync")),
    ("uses the network", re.compile(r"requests\.|urllib|socket\.|http\.client|fetch\(|\bcurl\b|\bwget\b")),
    ("runs shell commands", re.compile(r"subprocess|os\.system|child_process|\bexec\(|\beval\(")),
)


def command_evidence(command: str) -> str:
    """Resumen estructurado de efectos para Laya (al estilo CARE): qué borra, escribe, lee o envía.

    El cuerpo de un heredoc nunca llega como texto (puede traer instrucciones); solo sus efectos detectados.
    Sin efectos, se declara solo lectura: un `python3 - <<EOF` opaco ya no parece una amenaza desconocida.
    """
    kept, bodies = split_heredocs(command, keep_terminators=True)
    shell = "\n".join(kept)
    effects: list[str] = []
    for sc in extract_bash_tokens(shell):
        cmd, paths = parse_command_targets(sc)
        if cmd in DESTRUCTIVE_CMDS:
            effects += [f"deletes {p}" for p in paths]
        else:
            effects += [f"writes {t}" for t in write_targets(sc)]
        if cmd in NETWORK_CMDS:
            effects.append(f"network access ({cmd})")
        if cmd in {"sudo", "doas"} or re.match(r"(sudo|doas)\s", sc):
            effects.append("runs as root")
    if REMOTE_EXEC_RE.search(shell):
        effects.append("runs downloaded code")
    effects += [f"reads secrets {m.group(0)}" for m in SECRET_PATH_RE.finditer(shell)]
    for owner, body in bodies:
        heads = [parse_command_targets(sc)[0] for sc in extract_bash_tokens(owner) if "<<" in sc]
        if heads and INTERPRETER_RE.match(heads[0]):
            text = "\n".join(body)
            found = [label for label, pattern in SCRIPT_EFFECTS if pattern.search(text)]
            effects.append(f"inline {heads[0]} script ({len(body)} lines)" + (f" that {', '.join(found)}" if found else ""))
    return f"Command: {strip_for_classifier(command)}\nEffects: {'; '.join(effects) or 'read-only, no files modified'}"


# Escritura cuyo contenido decide el agente (no una herramienta): va por Write/Edit, nunca por Bash
CODE_WRITE_RE = re.compile(
    r"open\([^,)]*,\s*(mode\s*=\s*)?['\"][rbt]*[wax+]|write_text|write_bytes|writeFile|appendFile|createWriteStream"
)
INPLACE_FLAG_RE = re.compile(r"\s(-[a-zA-Z]*i[a-zA-Z]*|--in-place)\b|\s-i\s*inplace\b")
ECHO_TEE_RE = re.compile(r"\b(echo|printf)\b[^|;&\n]*\|\s*(sudo\s+)?tee\b")
WRITE_TOOL_HINT = (
    "Usa la tool Write (archivo nuevo o completo) o la tool Edit (cambio parcial) de Claude Code: "
    "el cambio queda revisable como diff."
)
EDIT_TOOL_HINT = "Usa la tool Edit de Claude Code (Read antes; replace_all para reemplazos repetidos)."


def _inline_code(line: str) -> list[str]:
    """Código pasado a intérpretes con -c / -e en una línea (python3 -c '...', node -e '...').

    Se analiza la línea completa: extract_bash_tokens corta en el ';' que vive dentro de las comillas.
    """
    try:
        tokens = shlex.split(line)
    except ValueError:
        return []
    return [
        tokens[i + 1]
        for i, tok in enumerate(tokens[:-1])
        if tok in {"-c", "-e", "--eval"} and any(INTERPRETER_RE.match(os.path.basename(t)) for t in tokens[:i])
    ]


SHELL_EXPANSION_RE = re.compile(r"\$(\?|\{[^}]*\}|\w+|\([^)]*\))")


def _is_status_echo(args: list[str], targets: list[str]) -> bool:
    """`echo "exit=$?" >> log`, `echo $SID > tmp/sid`: el contenido es una expansión, no texto redactado.

    Con `$?` (código de salida) se acepta una etiqueta de hasta 80 caracteres en una línea:
    `echo "server lint+typecheck+unit exit=$?" >> tmp/final.txt`.
    """
    text = " ".join(a for a in args if a not in targets)
    if not SHELL_EXPANSION_RE.search(text):
        return False
    literal = SHELL_EXPANSION_RE.sub("", text).strip()
    return len(literal) <= 20 or ("$?" in text and "\n" not in text and len(literal) <= 80)


DISPOSABLE_WRITE_EXTS = (".log",)


def _is_disposable_write(target: str) -> bool:
    """Destino desechable para texto redactado: ./tmp/ del proyecto, scratchpad /tmp/claude-* o un *.log.

    /tmp del sistema no cuenta (lo controla structure.system_tmp). Sin resolver ($(cmd), $1) tampoco.
    """
    if "$" in target or (target.startswith("/tmp/") and not SCRATCHPAD_RE.match(target)):
        return False
    return bool(TMP_SEGMENT_RE.search(target) or SCRATCHPAD_RE.match(target) or target.endswith(DISPOSABLE_WRITE_EXTS))


def _all_disposable(targets: list[str], variables: ShellVars) -> bool:
    for t in targets:
        values = expand_word(t, variables, None) if "$" in t else [t]
        if not values or not all(_is_disposable_write(v) for v in values):
            return False
    return bool(targets)


def agent_authored_write(command: str) -> str | None:
    """Motivo de bloqueo si el comando escribe un archivo con contenido redactado por el agente.

    La salida de herramientas (pytest > tmp/out.log, npm test | tee log, git, builds) no cuenta:
    ese contenido lo decide el programa, no el agente. Tampoco cuenta el texto redactado cuyo destino es
    desechable (./tmp/, scratchpad de la sesión, *.log): no es código del proyecto ni queda en un diff.
    """
    kept, bodies = split_heredocs(command, keep_terminators=True)
    shell = "\n".join(kept)
    variables: ShellVars = {}
    for sc in extract_bash_tokens(shell):
        if track_variables(sc, variables, None):
            continue
        cmd, paths = parse_command_targets(sc)
        targets = [t for t in redirect_targets(sc) if not t.startswith(HARMLESS_DEVICES)]
        if cmd in {"echo", "printf"} and targets and not _is_status_echo(paths, targets) and not _all_disposable(targets, variables):
            return f"'{cmd}' redirigido a {', '.join(targets)}. {WRITE_TOOL_HINT}"
        if "<<" in sc and (cmd in {"cat", "tee"} and (targets or (cmd == "tee" and paths))):
            if not _all_disposable(targets or paths, variables):
                return f"heredoc escrito con '{cmd}' en {', '.join(targets or paths)}. {WRITE_TOOL_HINT}"
        if cmd in {"sed", "perl", "ruby", "awk", "gawk"} and INPLACE_FLAG_RE.search(sc):
            edited = write_targets(sc) if cmd in INPLACE_EDITORS else []
            if not _all_disposable(edited, variables):
                return f"edición in-place con '{cmd}'. {EDIT_TOOL_HINT}"
    if any(CODE_WRITE_RE.search(code) for line in kept for code in _inline_code(line)):
        return f"script inline (-c / -e) que escribe archivos. {EDIT_TOOL_HINT}"
    if ECHO_TEE_RE.search(shell):
        return f"texto redactado enviado a 'tee'. {WRITE_TOOL_HINT}"
    for owner, body in bodies:
        heads = [parse_command_targets(sc)[0] for sc in extract_bash_tokens(owner) if "<<" in sc]
        if heads and INTERPRETER_RE.match(heads[0]) and CODE_WRITE_RE.search("\n".join(body)):
            return f"script inline de '{heads[0]}' (heredoc) que escribe archivos. {EDIT_TOOL_HINT}"
    return None


LAYA_MAX_STATE_CHARS = 1500  # ~512 tokens: lo que el checkpoint alcanza a leer, igual que en el entrenamiento


def load_laya_policy() -> dict[str, Any]:
    return json.loads(LAYA_POLICY_FILE.read_text())


def is_real_file(path: Path) -> bool:
    """False si falta o si es solo el puntero de Git LFS (instalación sin `git lfs pull`)."""
    if not path.is_file():
        return False
    with path.open("rb") as f:
        return f.read(len(LFS_POINTER_PREFIX)) != LFS_POINTER_PREFIX


def delta_path(policy: dict[str, Any]) -> Path:
    return MODELS_DIR / str(policy["profiles"]["finetuned"]["delta"])


def apply_finetuned_delta(router: Any, policy: dict[str, Any]) -> bool:
    """True si el delta afinado (safetensors: sin pickle) está disponible; False = usar zero-shot.

    El daemon lo aplica solo durante la petición de este hook: los demás hooks ven el checkpoint base.
    """
    if is_real_file(delta_path(policy)):
        return True
    log_step("laya.delta", result="missing", path=str(delta_path(policy)))
    return False


def laya_danger_score(state: str) -> tuple[str, float, float, dict[str, float]]:
    """(perfil, puntaje calibrado, umbral, probabilidades crudas) para un estado de command_evidence()."""
    policy = load_laya_policy()
    router = get_laya_router()
    finetuned = apply_finetuned_delta(router, policy)
    profile = "finetuned" if finetuned else "zeroshot"
    extra = {"delta": str(delta_path(policy))} if finetuned else {}
    answer = router.predict(state, DANGER_QUESTION, model=LAYA_MODEL, **extra)["answers"]["danger"]
    probs: dict[str, float] = {k: float(v) for k, v in answer["probabilities"].items()}
    cfg: dict[str, Any] = policy["profiles"][profile]
    features = [math.log(max(probs[k], 1e-6)) for k in sorted(probs)] + [1.0]
    z = sum(float(w) * x for w, x in zip(cfg["weights"], features))
    return profile, 1 / (1 + math.exp(-z)), float(cfg["threshold"]), probs


def _block(reason: str, target: str, rule: str, evidence: dict[str, Any] | None = None) -> None:
    emit_block(reason, hook_name="block_dangerous", tool_name="Bash", target=target, decided_by="python", rule=rule, evidence=evidence)


def _decide(
    decision: str, reason: str, target: str, rule: str, decided_by: str = "python", evidence: dict[str, Any] | None = None, context: str | None = None
) -> None:
    emit_decision(
        decision, reason, hook_name="block_dangerous", tool_name="Bash", target=target, decided_by=decided_by, rule=rule, evidence=evidence, context=context
    )


def evaluate_command_safety(command: str, cwd: str) -> None:
    """Evalúa el comando: bloqueos deterministas → confirmaciones deterministas → auto-allow → Laya (solo ask)."""
    if not command.strip():
        sys.exit(0)

    # 1. Patrones universalmente catastróficos
    for pat in CATASTROPHIC_PATTERNS:
        if re.search(pat, command):
            log_step("regex.catastrophic", result="match", pattern=pat)
            _block(f"Comando catalogado como catastrófico/destructivo (patrón de alta criticidad: {pat}).\nComando recibido: {command}",
                   command, "regex.catastrophic", {"pattern": pat})
    log_step("regex.catastrophic", result="no_match", checked=len(CATASTROPHIC_PATTERNS))

    # 1b. Reglas multi-agente deterministas: auto-aprobación, otro agente sin guardrails, PowerShell/cmd
    if SELF_APPROVAL_RE.search(command):
        _block("El agente no puede aprobar sus propias confirmaciones de Synapse: la aprobación la ejecuta el usuario "
               f"en su terminal.\nComando recibido: {command}", command, "structure.self_approval")
    wrapper = agent_wrapper(command)
    if wrapper and wrapper[0] == "block":
        _block(f"Lanzar otro agente sin sus protecciones no está permitido: {wrapper[1]}.\nComando recibido: {command}",
               command, "structure.agent_wrapper_unsafe")
    windows = windows_rule(command)
    if windows and windows[0] == "block":
        _block(f"{windows[2]}\nComando recibido: {command}", command, windows[1])

    # 2. Análisis estructural por sub-comando, con variables resueltas: fuera del entorno git del proyecto,
    #    /tmp del sistema, carpeta .git y objetivos de borrado
    roots = allowed_roots(cwd)
    eff_cwd: str | None = cwd  # None tras un `cd` a un destino no determinable
    prev_cwd: str | None = None
    variables: ShellVars = {}
    destructive_targets: list[str] = []
    recoverable_targets: list[str] = []
    git_refs: list[str] = []
    system_tmp_redirects: list[str] = []
    all_targets_disposable = True
    all_targets_safe = True  # desechables o recuperables con git checkout
    has_destructive_cmd = False
    for sc in extract_bash_tokens(command):
        if track_variables(sc, variables, eff_cwd):
            continue
        cmd, paths = parse_command_targets(sc)
        # Leer .git (cat .git, ls .git/worktrees, test -f .git/MERGE_HEAD) no cambia el historial
        if not is_read_only(sc, eff_cwd or cwd, roots):
            git_refs += git_dir_refs(sc, variables, eff_cwd)
        if cmd == "cd":
            if sc.split()[1:2] == ["-"]:  # vuelve al directorio anterior (OLDPWD); desconocido al inicio
                eff_cwd, prev_cwd = prev_cwd, eff_cwd
            else:
                prev_cwd = eff_cwd
                dest = resolve_candidates(paths[0] if paths else "~", variables, eff_cwd)
                eff_cwd = dest[0] if dest and len(dest) == 1 else None
            if eff_cwd and is_outside_project(eff_cwd, "/", roots):
                log_step("structure.cd_outside", target=eff_cwd, roots=roots)
            continue
        redirects = redirect_targets(sc)
        for t in write_targets(sc):
            candidates = resolve_candidates(t, variables, eff_cwd)
            if candidates is None:
                log_step("structure.unresolved", subcmd=sc, target=t)
                continue
            # `rm link` borra el enlace, no su destino; `rm link/` y las escrituras sí lo siguen
            follow = not (cmd in DESTRUCTIVE_CMDS and not t.endswith("/"))
            for resolved in candidates:
                if is_system_tmp(resolved, "/") and t in redirects:
                    # Salida de un programa redirigida a /tmp del sistema: no destruye nada, pero queda fuera del
                    # proyecto. Se confirma (después de los bloqueos) en vez de bloquear.
                    log_step("structure.system_tmp_redirect", result="match", subcmd=sc, target=resolved)
                    system_tmp_redirects.append(resolved)
                    continue
                if is_system_tmp(resolved, "/"):
                    log_step("structure.system_tmp", result="match", subcmd=sc, target=resolved)
                    _block(f"El comando '{cmd}' apunta a /tmp del sistema operativo ({resolved}). Usa ./tmp/ dentro del proyecto.",
                           resolved, "structure.system_tmp", {"subcmd": sc, "target": resolved})
                if is_outside_project(resolved, "/", roots, follow_last=follow):
                    log_step("structure.outside_project", result="match", subcmd=sc, target=resolved, roots=roots)
                    _block(
                        f"Prohibido modificar rutas fuera del proyecto ({', '.join(roots)}): '{cmd}' sobre {resolved}.\n"
                        f"Comando recibido: {command}",
                        resolved,
                        "structure.outside_project",
                        {"subcmd": sc, "target": resolved, "roots": list(roots)},
                    )
        if cmd not in DESTRUCTIVE_CMDS and not (is_routine(sc) or is_read_only(sc, eff_cwd or cwd, roots)):
            all_targets_safe = False  # el auto-allow recuperable no puede aprobar de rebote otro sub-comando
        if cmd in DESTRUCTIVE_CMDS:
            has_destructive_cmd = True
            if not paths:
                all_targets_disposable = all_targets_safe = False
            for p in paths:
                destructive_targets.append(p)
                candidates = resolve_candidates(p, variables, eff_cwd)
                disposable = bool(candidates) and all(is_disposable_target(c, eff_cwd or os.path.dirname(c)) for c in candidates or [])
                # `rm -rf link/` entra al destino del enlace: lo recuperable es el enlace, no lo que borra
                through_link = p.endswith("/") and any(os.path.islink(c.rstrip("/")) for c in candidates or [])
                recoverable = not disposable and not through_link and bool(candidates) and all(
                    is_disposable_target(c, eff_cwd or os.path.dirname(c)) or is_git_recoverable(c, eff_cwd or os.path.dirname(c))
                    for c in candidates or []
                )
                all_targets_disposable = all_targets_disposable and disposable
                all_targets_safe = all_targets_safe and (disposable or recoverable)
                if recoverable:
                    recoverable_targets += candidates or []
                log_step("structure.delete_target", target=p, resolved=candidates, disposable=disposable, recoverable=recoverable)
    log_step("structure", result="ok", roots=roots)

    # 2b. Escribir archivos con contenido propio desde Bash está prohibido: para eso existen Write/Edit
    authored = agent_authored_write(command)
    if authored:
        log_step("structure.bash_write", result="match", reason=authored)
        _block(f"Prohibido escribir archivos desde Bash: {authored}", command, "structure.bash_write", {"detail": authored})
    log_step("structure.bash_write", result="no_match")

    # 2c. La carpeta .git guarda todo el historial: cualquier comando que la involucre pide confirmación
    if git_refs:
        log_step("structure.git_dir", result="match", refs=git_refs)
        _decide("ask", f"El comando involucra la carpeta .git del repositorio ({', '.join(git_refs)}). Se requiere confirmación explícita.",
                command, "structure.git_dir", evidence={"refs": git_refs})

    # 2d. Salida redirigida a /tmp del sistema: confirmación (el scratchpad /tmp/claude-* sigue permitido)
    if system_tmp_redirects:
        _decide("ask", f"La salida se redirige a /tmp del sistema ({', '.join(system_tmp_redirects)}). Usa ./tmp/ del proyecto "
                "o el scratchpad de la sesión; confirma si es intencional.", command, "structure.system_tmp_redirect",
                evidence={"targets": system_tmp_redirects})

    # 2e. Otro agente en modo no interactivo y borrados recursivos de Windows: confirmación determinista
    if wrapper:
        _decide("ask", f"El comando lanza otro agente: {wrapper[1]}. Se requiere confirmación explícita.", command,
                "structure.agent_wrapper")
    if windows:
        _decide("ask", windows[2], command, windows[1])

    # 3. Confirmaciones deterministas, antes del auto-allow (un 'allow' salta el sistema de permisos de Claude Code)
    pat = ask_pattern_hit(command)
    if pat:
        log_step("regex.ask", result="match", pattern=pat)
        _decide("ask", f"Operación difícil de revertir (patrón: {pat}). Se requiere confirmación explícita.", command, "regex.ask", evidence={"pattern": pat})
    log_step("regex.ask", result="no_match", checked=len(ASK_PATTERNS))

    # 4. Borrado exclusivamente de temporales desechables: aprobado sin preguntar
    targets = ", ".join(destructive_targets)
    if has_destructive_cmd and all_targets_disposable and destructive_targets:
        _decide("allow", f"Auto-aprobado: todos los objetivos son temporales desechables ({targets}).", targets, "structure.disposable_delete",
                evidence={"targets": destructive_targets})

    # 4b. Borrado de contenido commiteado sin cambios locales: git lo restaura idéntico, no hace falta preguntar.
    #     El recordatorio va como additionalContext para que la sesión sepa cómo deshacerlo.
    if has_destructive_cmd and all_targets_safe and recoverable_targets:
        # Rutas absolutas con -C al repo dueño: el hint funciona tras un `cd` y en repos anidados o submódulos
        hint = " && ".join(
            f"git -C {shlex.quote(os.path.dirname(t))} checkout HEAD -- {shlex.quote(t)}" for t in dict.fromkeys(recoverable_targets)
        )
        _decide("allow", f"Auto-aprobado: los objetivos están commiteados sin cambios locales ({targets}).", targets, "structure.git_recoverable_delete",
                evidence={"targets": destructive_targets, "recoverable": recoverable_targets},
                context=f"Synapse: se puede hacer git checkout para recuperar el archivo ({hint}).")

    # 5. Borrado de archivos permanentes: la confirmación es segura, Laya no cambiaría la decisión
    if has_destructive_cmd and not all_targets_disposable:
        _decide("ask", f"El comando intenta eliminar archivos permanentes o fuera de tmp/ ({targets}). Se requiere confirmación explícita.",
                targets, "structure.permanent_delete", evidence={"targets": destructive_targets})

    # 6. Laya solo para lo que el código no resuelve; solo escala a 'ask', nunca bloquea.
    #    Puntaje calibrado (Platt) sobre el modelo afinado; métricas en models/laya-block-dangerous.json.
    if is_routine(command):
        log_step("laya", result="skipped", reason="comando rutinario")
    elif is_read_only(command, cwd, roots):
        log_step("laya", result="skipped", reason="solo lectura (determinista)")
    elif is_lockfile_sync(command, cwd, roots):
        log_step("laya", result="skipped", reason="sincronización de lockfile (determinista)")
    elif is_dev_routine(command, cwd, roots):
        log_step("laya", result="skipped", reason="desarrollo rutinario (determinista)")
    elif not should_use_laya("block_dangerous"):
        log_step("laya", result="skipped", reason=laya_skip_reason("block_dangerous"))
    else:
        try:
            started = datetime.now()
            state = command_evidence(command)[:LAYA_MAX_STATE_CHARS]
            profile, score, threshold, probs = laya_danger_score(state)
            flagged = score >= threshold
            log_step(
                "laya",
                result="flagged" if flagged else "ok",
                input=state,
                profile=profile,
                p_danger=round(probs["A"], 4),
                score=round(score, 4),
                threshold=threshold,
                laya_ms=round((datetime.now() - started).total_seconds() * 1000, 1),
            )
            if flagged:
                _decide("ask", f"el modelo {profile} estima que este comando requiere confirmación (puntaje {score:.3f} ≥ umbral {threshold:.3f}).",
                        command, "laya.danger", decided_by="laya",
                        evidence={"profile": profile, "score": round(score, 4), "threshold": threshold, "p_danger": round(probs["A"], 4)})
        except Exception as exc:
            log_step("laya", result="error", error=repr(exc))

    record_audit_log("ALLOW", "block_dangerous", "Bash", command, "Sin objeciones: Claude Code aplica sus permisos normales", rule="sin_objeciones")
    sys.exit(0)


def evaluate(event: hosts.HookEvent) -> None:
    """Solo aplica a comandos de shell (Bash, run_command, shell… según el host)."""
    if event.kind != "shell":
        return
    evaluate_command_safety(event.command, event.cwd or os.getcwd())


def main() -> None:
    data = read_hook_input("block_dangerous")
    if not data:
        sys.exit(0)
    evaluate(hosts.normalize("claude", data))


if __name__ == "__main__":
    run_main(main)
