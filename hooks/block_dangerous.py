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
import sys
from pathlib import Path
from typing import Any

# Agregar directorio actual a sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

from common import (
    HARMLESS_DEVICES,
    TMP_SEGMENT_RE,
    ShellVars,
    allowed_roots,
    emit_block,
    emit_decision,
    expand_word,
    extract_bash_tokens,
    get_laya_router,
    is_disposable_target,
    is_outside_project,
    is_system_tmp,
    log_step,
    parse_command_targets,
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
    r"git\s+push\s+.*(--force|-f)\b.*\b(origin\s+)?(main|master|dev)\b",
    r"git\s+push\s+.*\b(origin\s+)?(main|master|dev)\b.*(--force|-f)\b",
    r"git\s+push\s+--force-with-lease\s+.*\b(origin\s+)?(main|master|dev)\b",
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
    r"git\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)",
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
    # Servidor HTTP que expone el home o la raíz del sistema a la red
    r"\bhttp\.server\b.*\s(-d|--directory)[=\s]+(~|\$HOME|/home/[^/\s]+|/root|/)/?(\s|$)",
]

DESTRUCTIVE_CMDS = {"rm", "unlink", "rmdir", "shred"}

# Comandos de archivos que escriben o destruyen sus argumentos (todos) o solo su destino (último)
MUTATING_CMDS = {"rm", "unlink", "rmdir", "shred", "mv", "truncate", "chmod", "chown", "tee", "touch"}
INPLACE_EDITORS = {"sed", "perl"}  # solo con -i
DEST_ONLY_CMDS = {"cp", "rsync", "install", "ln"}


def write_targets(subcmd: str) -> list[str]:
    """Rutas que un sub-comando modifica: redirecciones de salida + argumentos de comandos de escritura."""
    targets = redirect_targets(subcmd)
    cmd, paths = parse_command_targets(subcmd)
    if cmd in MUTATING_CMDS or (cmd in INPLACE_EDITORS and re.search(r"\s-[a-zA-Z]*i", subcmd)):
        targets += paths
    elif cmd in DEST_ONLY_CMDS and paths:
        targets.append(paths[-1])
    elif cmd == "dd":
        targets += [p[3:] for p in paths if p.startswith("of=")]
    elif cmd == "find" and paths and re.search(r"\s-(delete|exec\s+(rm|shred|mv|truncate))\b", subcmd):
        targets.append(paths[0])
    return targets


def touches_git_dir(path: str) -> bool:
    """.git/ guarda todo el historial: cualquier comando de archivos que la toque pide confirmación."""
    return ".git" in os.path.normpath(path).split(os.sep)


# Flags cuyo valor es un patrón de exclusión o búsqueda, no una ruta que se lea o escriba (rg --glob '!.git')
PATTERN_FLAGS = {
    "--exclude", "--exclude-dir", "--ignore", "--ignore-glob", "--ignore-dir", "-E", "-I", "-g", "--glob",
    "-name", "-iname", "-path", "-ipath", "-not", "!", "--filter", "-x",
}


def git_dir_refs(subcmd: str, variables: ShellVars, cwd: str | None) -> list[str]:
    """Argumentos del sub-comando que apuntan a una carpeta .git (también vía variables: d=.git; rm -rf $d)."""
    try:
        tokens = shlex.split(subcmd, posix=True)
    except ValueError:
        return []
    refs: list[str] = []
    prev = ""
    for tok in tokens:
        if prev not in PATTERN_FLAGS and not tok.startswith("-"):
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
            expr = paths[0] if cmd == "sed" and paths else None
            effects += [f"writes {t}" for t in write_targets(sc) if t != expr]
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
    """`echo "exit=$?" >> log`, `echo $SID > tmp/sid`: el contenido es una expansión, no texto redactado."""
    text = " ".join(a for a in args if a not in targets)
    return bool(SHELL_EXPANSION_RE.search(text)) and len(SHELL_EXPANSION_RE.sub("", text).strip()) <= 20


def agent_authored_write(command: str) -> str | None:
    """Motivo de bloqueo si el comando escribe un archivo con contenido redactado por el agente.

    La salida de herramientas (pytest > tmp/out.log, npm test | tee log, git, builds) no cuenta:
    ese contenido lo decide el programa, no el agente.
    """
    kept, bodies = split_heredocs(command, keep_terminators=True)
    shell = "\n".join(kept)
    for sc in extract_bash_tokens(shell):
        cmd, paths = parse_command_targets(sc)
        targets = [t for t in redirect_targets(sc) if not t.startswith(HARMLESS_DEVICES)]
        if cmd in {"echo", "printf"} and targets and not _is_status_echo(paths, targets):
            return f"'{cmd}' redirigido a {', '.join(targets)}. {WRITE_TOOL_HINT}"
        if "<<" in sc and (cmd in {"cat", "tee"} and (targets or (cmd == "tee" and paths))):
            return f"heredoc escrito con '{cmd}' en {', '.join(targets or paths)}. {WRITE_TOOL_HINT}"
        if cmd in {"sed", "perl", "ruby", "awk", "gawk"} and INPLACE_FLAG_RE.search(sc):
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


def _decide(decision: str, reason: str, target: str, rule: str, decided_by: str = "python", evidence: dict[str, Any] | None = None) -> None:
    emit_decision(decision, reason, hook_name="block_dangerous", tool_name="Bash", target=target, decided_by=decided_by, rule=rule, evidence=evidence)


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

    # 2. Análisis estructural por sub-comando, con variables resueltas: fuera del entorno git del proyecto,
    #    /tmp del sistema, carpeta .git y objetivos de borrado
    roots = allowed_roots(cwd)
    eff_cwd: str | None = cwd  # None tras un `cd` a un destino no determinable
    variables: ShellVars = {}
    destructive_targets: list[str] = []
    git_refs: list[str] = []
    all_targets_disposable = True
    has_destructive_cmd = False
    for sc in extract_bash_tokens(command):
        if track_variables(sc, variables, eff_cwd):
            continue
        cmd, paths = parse_command_targets(sc)
        git_refs += git_dir_refs(sc, variables, eff_cwd)
        if cmd == "cd":
            dest = resolve_candidates(paths[0] if paths else "~", variables, eff_cwd)
            eff_cwd = dest[0] if dest and len(dest) == 1 else None
            if eff_cwd and is_outside_project(eff_cwd, "/", roots):
                log_step("structure.cd_outside", target=eff_cwd, roots=roots)
            continue
        for t in write_targets(sc):
            candidates = resolve_candidates(t, variables, eff_cwd)
            if candidates is None:
                log_step("structure.unresolved", subcmd=sc, target=t)
                continue
            # `rm link` borra el enlace, no su destino; `rm link/` y las escrituras sí lo siguen
            follow = not (cmd in DESTRUCTIVE_CMDS and not t.endswith("/"))
            for resolved in candidates:
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
        if cmd in DESTRUCTIVE_CMDS:
            has_destructive_cmd = True
            if not paths:
                all_targets_disposable = False
            for p in paths:
                destructive_targets.append(p)
                candidates = resolve_candidates(p, variables, eff_cwd)
                disposable = bool(candidates) and all(is_disposable_target(c, eff_cwd or os.path.dirname(c)) for c in candidates or [])
                all_targets_disposable = all_targets_disposable and disposable
                log_step("structure.delete_target", target=p, resolved=candidates, disposable=disposable)
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

    # 3. Confirmaciones deterministas, antes del auto-allow (un 'allow' salta el sistema de permisos de Claude Code)
    for pat in ASK_PATTERNS:
        if re.search(pat, command):
            log_step("regex.ask", result="match", pattern=pat)
            _decide("ask", f"Operación difícil de revertir (patrón: {pat}). Se requiere confirmación explícita.", command, "regex.ask", evidence={"pattern": pat})
    log_step("regex.ask", result="no_match", checked=len(ASK_PATTERNS))

    # 4. Borrado exclusivamente de temporales desechables: aprobado sin preguntar
    targets = ", ".join(destructive_targets)
    if has_destructive_cmd and all_targets_disposable and destructive_targets:
        _decide("allow", f"Auto-aprobado: todos los objetivos son temporales desechables ({targets}).", targets, "structure.disposable_delete",
                evidence={"targets": destructive_targets})

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
    elif not should_use_laya():
        log_step("laya", result="skipped", reason="modo fallback")
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


def main() -> None:
    data = read_hook_input("block_dangerous")
    if not data:
        sys.exit(0)

    # Solo aplica a ejecuciones de la herramienta Bash
    if data.get("tool_name") != "Bash":
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    cwd = data.get("cwd") or os.getcwd()

    evaluate_command_safety(command, cwd)


if __name__ == "__main__":
    run_main(main)
