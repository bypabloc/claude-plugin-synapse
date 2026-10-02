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

import os
import re
import shlex
import sys

# Agregar directorio actual a sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

from common import (
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

# Preguntas tipadas de Laya para evaluación de comandos Bash
COMMAND_EVAL_QUESTIONS = {
    "danger_type": {
        "type": "choice",
        "instructions": "Classify the security risk of this shell command execution",
        "criteria": {
            "catastrophic": "root filesystem deletion, disk formatting, fork bomb, device raw writing, force push to master/main",
            "temporary_cleanup": "removing or cleaning cache, log files, tmp directory or temporary build artifacts",
            "safe_operation": "standard builds, tests, git status, safe package installs, directory listing",
            "sensitive_mutation": "modifying or deleting tracked source code or project configuration outside temporary directories"
        }
    },
    "is_catastrophic": {
        "type": "choice",
        "instructions": "Is this command irreversibly destructive to the operating system or git history?",
        "criteria": {
            "yes": "catastrophic command that damages OS or overwrites main/master branch history",
            "no": "standard or recoverable operation"
        }
    }
}

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


def strip_for_classifier(command: str) -> str:
    """Quita comentarios y cuerpos de heredoc antes de Laya.

    Ambos son texto libre que el agente (o una inyección) controla: medido en 45 comandos, un comentario
    cambia la decisión de Laya en ~15% de los casos y puede volver inofensivo uno malicioso.
    """
    lines = command.splitlines()
    kept: list[str] = []
    delimiter: str | None = None
    for line in lines:
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        match = HEREDOC_RE.search(line)
        if match:
            delimiter = match.group(2)
        kept.append(line)
    try:
        return " ".join(" ".join(shlex.split(line, comments=True)) for line in kept).strip()
    except ValueError:
        return " ".join(kept)


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

    # 3. Confirmaciones deterministas, antes del auto-allow (un 'allow' salta el sistema de permisos de Claude Code)
    for pat in ASK_PATTERNS:
        if re.search(pat, command):
            log_step("regex.ask", result="match", pattern=pat)
            _decide("ask", f"Operación difícil de revertir (patrón: {pat}). Se requiere confirmación explícita.", command)
    log_step("regex.ask", result="no_match", checked=len(ASK_PATTERNS))

    # 4. Borrado exclusivamente de temporales desechables: aprobado sin preguntar
    if has_destructive_cmd and all_targets_disposable and destructive_targets:
        _decide("allow", f"Auto-aprobado: todos los objetivos son temporales desechables ({', '.join(destructive_targets)}).", ", ".join(destructive_targets))

    # 5. Laya System 1 solo escala a 'ask', nunca bloquea: medido en 45 comandos detecta 6/20 maliciosos
    #    y marca 3/25 benignos, demasiado ruido para un bloqueo duro. Los bloqueos son solo deterministas.
    if is_routine(command):
        log_step("laya", result="skipped", reason="comando rutinario")
    elif not should_use_laya():
        log_step("laya", result="skipped", reason="modo fallback")
    else:
        try:
            started = datetime.now()
            classifier_input = strip_for_classifier(command)
            answers = get_laya_router().predict(classifier_input, COMMAND_EVAL_QUESTIONS)["answers"]
            danger_type = answers["danger_type"]["choice"]
            danger_conf = answers["danger_type"]["answer_confidence"]
            is_catastrophic = answers["is_catastrophic"]["choice"]
            catastrophic_conf = answers["is_catastrophic"]["answer_confidence"]
            # Si danger_type dice safe_operation, is_catastrophic=yes es una contradicción del modelo, no una señal
            cat_signal = is_catastrophic == "yes" and catastrophic_conf >= 0.65 and danger_type != "safe_operation"
            flagged = cat_signal or (danger_type == "catastrophic" and danger_conf >= 0.50)
            log_step(
                "laya",
                result="flagged" if flagged else "ok",
                input=classifier_input,
                danger_type=danger_type,
                danger_conf=round(danger_conf, 3),
                is_catastrophic=is_catastrophic,
                catastrophic_conf=round(catastrophic_conf, 3),
                laya_ms=round((datetime.now() - started).total_seconds() * 1000, 1),
            )
            if flagged:
                _decide("ask", f"Laya System 1 marcó el comando como potencialmente destructivo ({danger_type}). Se requiere confirmación explícita.", command)
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
