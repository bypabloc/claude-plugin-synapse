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
  5. Bloqueo determinístico de cualquier escritura en .git/ por comandos de archivos.
  6. Fallback automático a las reglas de seguridad determinísticas si no hay GPU o falla Laya.

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
    emit_block,
    emit_decision,
    extract_bash_tokens,
    get_laya_router,
    is_disposable_target,
    is_outside_project,
    is_system_tmp,
    log_step,
    parse_command_targets,
    project_root,
    read_hook_input,
    record_audit_log,
    redirect_targets,
    resolve_path,
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
    """.git/ guarda todo el historial: ningún comando de archivos puede escribir ahí (solo los propios de git)."""
    return ".git" in os.path.normpath(path).split(os.sep)


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

_delta_state: dict[str, bool] = {}  # caché por proceso: el delta se aplica una sola vez


def load_laya_policy() -> dict[str, Any]:
    return json.loads(LAYA_POLICY_FILE.read_text())


def is_real_file(path: Path) -> bool:
    """False si falta o si es solo el puntero de Git LFS (instalación sin `git lfs pull`)."""
    if not path.is_file():
        return False
    with path.open("rb") as f:
        return f.read(len(LFS_POINTER_PREFIX)) != LFS_POINTER_PREFIX


def apply_finetuned_delta(router: Any, policy: dict[str, Any]) -> bool:
    """Carga el delta afinado (safetensors: sin pickle) sobre el checkpoint inglés; False = usar zero-shot."""
    if "applied" not in _delta_state:
        path = MODELS_DIR / str(policy["profiles"]["finetuned"]["delta"])
        _delta_state["applied"] = is_real_file(path)
        if _delta_state["applied"]:
            from safetensors.torch import load_file
            router.load(LAYA_MODEL).model.load_state_dict(load_file(str(path)), strict=False)
        else:
            log_step("laya.delta", result="missing", path=str(path))
    return _delta_state["applied"]


def laya_danger_score(state: str) -> tuple[str, float, float, dict[str, float]]:
    """(perfil, puntaje calibrado, umbral, probabilidades crudas) para un estado de command_evidence()."""
    policy = load_laya_policy()
    router = get_laya_router()
    profile = "finetuned" if apply_finetuned_delta(router, policy) else "zeroshot"
    answer = router.predict(state, DANGER_QUESTION, model=LAYA_MODEL)["answers"]["danger"]
    probs: dict[str, float] = {k: float(v) for k, v in answer["probabilities"].items()}
    cfg: dict[str, Any] = policy["profiles"][profile]
    features = [math.log(max(probs[k], 1e-6)) for k in sorted(probs)] + [1.0]
    z = sum(float(w) * x for w, x in zip(cfg["weights"], features))
    return profile, 1 / (1 + math.exp(-z)), float(cfg["threshold"]), probs


def _block(reason: str, target: str) -> None:
    emit_block(reason, exit_code=2, hook_name="block_dangerous", tool_name="Bash", target=target)


def _decide(decision: str, reason: str, target: str) -> None:
    emit_decision(decision, reason, hook_name="block_dangerous", tool_name="Bash", target=target)


def evaluate_command_safety(command: str, cwd: str) -> None:
    """Evalúa el comando: bloqueos deterministas → confirmaciones deterministas → auto-allow → Laya (solo ask)."""
    if not command.strip():
        sys.exit(0)

    # 1. Patrones universalmente catastróficos
    for pat in CATASTROPHIC_PATTERNS:
        if re.search(pat, command):
            log_step("regex.catastrophic", result="match", pattern=pat)
            _block(f"Comando catalogado como catastrófico/destructivo (patrón de alta criticidad).\nComando recibido: {command}", command)
    log_step("regex.catastrophic", result="no_match", checked=len(CATASTROPHIC_PATTERNS))

    # 2. Análisis estructural por sub-comando: .git/, fuera del proyecto, /tmp del sistema, objetivos de borrado
    root = project_root(cwd)
    eff_cwd = cwd
    destructive_targets: list[str] = []
    all_targets_disposable = True
    has_destructive_cmd = False
    for sc in extract_bash_tokens(command):
        cmd, paths = parse_command_targets(sc)
        if cmd == "cd":
            eff_cwd = resolve_path(paths[0] if paths else "~", eff_cwd)
            continue
        for t in write_targets(sc):
            resolved = resolve_path(t, eff_cwd)
            if touches_git_dir(resolved):
                log_step("structure.git_dir", result="match", subcmd=sc, target=resolved)
                _block(f"Prohibido modificar .git/ (contiene todo el historial): '{cmd}' sobre {t}.\nComando recibido: {command}", sc)
            if is_system_tmp(t, eff_cwd):
                log_step("structure.system_tmp", result="match", subcmd=sc, target=resolved)
                _block(f"El comando '{cmd}' apunta a /tmp del sistema operativo ({t}). Usa ./tmp/ dentro del proyecto.", t)
            if is_outside_project(t, eff_cwd, root):
                log_step("structure.outside_project", result="match", subcmd=sc, target=resolved, root=root)
                _block(f"Prohibido modificar rutas fuera del proyecto ({root}): '{cmd}' sobre {resolved}.\nComando recibido: {command}", resolved)
        if cmd in DESTRUCTIVE_CMDS:
            has_destructive_cmd = True
            if not paths:
                all_targets_disposable = False
            for p in paths:
                destructive_targets.append(p)
                disposable = is_disposable_target(p, eff_cwd)
                all_targets_disposable = all_targets_disposable and disposable
                log_step("structure.delete_target", target=resolve_path(p, eff_cwd), disposable=disposable)
    log_step("structure", result="ok", root=root)

    # 2b. Escribir archivos con contenido propio desde Bash está prohibido: para eso existen Write/Edit
    authored = agent_authored_write(command)
    if authored:
        log_step("structure.bash_write", result="match", reason=authored)
        _block(f"Prohibido escribir archivos desde Bash: {authored}", command)
    log_step("structure.bash_write", result="no_match")

    # 3. Confirmaciones deterministas, antes del auto-allow (un 'allow' salta el sistema de permisos de Claude Code)
    for pat in ASK_PATTERNS:
        if re.search(pat, command):
            log_step("regex.ask", result="match", pattern=pat)
            _decide("ask", f"Operación difícil de revertir (patrón: {pat}). Se requiere confirmación explícita.", command)
    log_step("regex.ask", result="no_match", checked=len(ASK_PATTERNS))

    # 4. Borrado exclusivamente de temporales desechables: aprobado sin preguntar
    if has_destructive_cmd and all_targets_disposable and destructive_targets:
        _decide("allow", f"Auto-aprobado: todos los objetivos son temporales desechables ({', '.join(destructive_targets)}).", ", ".join(destructive_targets))

    # 5. Laya solo escala a 'ask', nunca bloquea; los bloqueos son solo deterministas.
    #    Puntaje calibrado (Platt) sobre el modelo afinado; métricas en models/laya-block-dangerous.json.
    if is_routine(command):
        log_step("laya", result="skipped", reason="comando rutinario")
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
                _decide("ask", f"Laya ({profile}) estima que este comando requiere confirmación (puntaje {score:.3f} ≥ {threshold:.3f}).", command)
        except Exception as exc:
            log_step("laya", result="error", error=repr(exc))

    # 6. Borrado de archivos permanentes dentro del proyecto
    if has_destructive_cmd and not all_targets_disposable:
        _decide("ask", f"El comando intenta eliminar archivos permanentes o fuera de tmp/ ({', '.join(destructive_targets)}). Se requiere confirmación explícita.", ", ".join(destructive_targets))

    record_audit_log("ALLOW", "block_dangerous", "Bash", command, "Sin objeciones: Claude Code aplica sus permisos normales")
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
    main()
