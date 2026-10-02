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
     fork bombs, mkfs, dd a dispositivos, git push force a main/master).
  3. Evaluación semántica con Laya System 1 para detectar ataques no contemplados en firmas.
  4. Fallback automático a las reglas de seguridad determinísticas si no hay GPU o falla Laya.

Vínculos con la investigación en docs/research/laya/:
  - docs/research/laya/08-casos-de-uso-y-patrones-arquitecturales.md:
      Patrón "Guardrails de Seguridad y Pre-Gating para LLMs".
  - docs/research/laya/03-guia-de-uso-y-primitivas.md:
      Configuración de preguntas tipadas 'choice' con criterios semánticos neutrales.
  - docs/research/laya/10-tips-comunidad-antipatrones-y-limites-honestos.md:
      Uso de answer_confidence (AUROC 0.77) en lugar de act_probability y neutralización del binary bias.

Ejemplos de Uso en CLI:
  # 1. Caso auto-aprobado (archivo temporal):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "rm -f ./tmp/cache.log"}}' | python src/block_dangerous.py --gpu
  -> {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", ...}} [exit 0]

  # 2. Caso comando catastrófico (bloqueo):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}' | python src/block_dangerous.py --gpu
  -> 🚫 BLOCKED: Comando catalogado como catastrófico/destructivo [exit 2]

  # 3. Caso borrado permanente fuera de tmp (solicita confirmación al usuario):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "rm src/app.py"}}' | python src/block_dangerous.py --gpu
  -> {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", ...}} [exit 0]
"""

from __future__ import annotations

import os
import re
import sys

# Agregar directorio actual a sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (
    emit_block,
    emit_decision,
    extract_bash_tokens,
    get_laya_router,
    is_disposable_target,
    is_system_tmp,
    parse_command_targets,
    read_hook_input,
    record_audit_log,
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
    r":\(\)\{.*\}",  # fork bomb
    r"mkfs\.",
    r"dd\s+if=.*\s+of=/dev/",
    r"git\s+push\s+.*(--force|-f)\b.*\b(origin\s+)?(main|master|dev)\b",
    r"git\s+push\s+.*\b(origin\s+)?(main|master|dev)\b.*(--force|-f)\b",
    r"git\s+push\s+--force-with-lease\s+.*\b(origin\s+)?(main|master|dev)\b",
    r"git\s+branch\s+(-D|-d\s+--force|--delete\s+--force)\s+(main|master|dev)\b",
]

DESTRUCTIVE_CMDS = {"rm", "unlink", "rmdir"}


def evaluate_command_safety(command: str, cwd: str) -> None:
    """Evalúa la seguridad del comando combinando análisis Laya System 1 y reglas de temporales."""
    if not command.strip():
        sys.exit(0)

    # 1. Bloqueo determinístico inmediato para patrones universalmente catastróficos
    for pat in CATASTROPHIC_PATTERNS:
        if re.search(pat, command):
            emit_block(
                f"Comando catalogado como catastrófico/destructivo (patrón de alta criticidad).\n"
                f"Comando recibido: {command}",
                exit_code=2,
                hook_name="block_dangerous",
                tool_name="Bash",
                target=command,
            )

    subcmds = extract_bash_tokens(command)
    all_targets_disposable = True
    has_destructive_cmd = False
    destructive_targets: list[str] = []

    # 2. Inspeccionar comandos de borrado y sus objetivos
    for sc in subcmds:
        cmd, paths = parse_command_targets(sc)
        if cmd in DESTRUCTIVE_CMDS:
            has_destructive_cmd = True
            if not paths:
                all_targets_disposable = False
                continue
            for p in paths:
                # Comprobar si apunta al /tmp del sistema operativo fuera de scratchpad
                if is_system_tmp(p, cwd):
                    emit_block(
                        f"El comando '{cmd}' apunta a /tmp del sistema operativo ({p}). "
                        "Usa ./tmp/ dentro del proyecto.",
                        exit_code=2,
                        hook_name="block_dangerous",
                        tool_name="Bash",
                        target=p,
                    )
                destructive_targets.append(p)
                if not is_disposable_target(p, cwd):
                    all_targets_disposable = False

    # 3. AUTO-APROBACIÓN INMEDIATA DE TEMPORALES (Requerimiento crítico del usuario)
    # Si es una eliminación y TODOS los objetivos son temporales (tmp/, scratchpad, gitignored), autorizar sin preguntar
    if has_destructive_cmd and all_targets_disposable and destructive_targets:
        emit_decision(
            "allow",
            f"Auto-aprobado: Todos los archivos a eliminar son temporales desechables ({', '.join(destructive_targets)}).",
            hook_name="block_dangerous",
            tool_name="Bash",
            target=", ".join(destructive_targets),
        )

    # 4. Evaluación semántica con Laya System 1 (si GPU/CUDA está disponible o se solicitó Laya)
    if should_use_laya():
        try:
            router = get_laya_router()
            res = router.predict(command, COMMAND_EVAL_QUESTIONS)
            answers = res["answers"]

            danger_type = answers["danger_type"]["choice"]
            danger_conf = answers["danger_type"]["answer_confidence"]
            is_catastrophic = answers["is_catastrophic"]["choice"]
            catastrophic_conf = answers["is_catastrophic"]["answer_confidence"]

            # Bloqueo si Laya detecta intencionalidad catastrófica
            if (is_catastrophic == "yes" and catastrophic_conf >= 0.65) or (danger_type == "catastrophic" and danger_conf >= 0.50):
                emit_block(
                    f"Comando catalogado como catastrófico/destructivo por Laya System 1 ({danger_type}).\n"
                    f"Comando recibido: {command}",
                    exit_code=2,
                    hook_name="block_dangerous",
                    tool_name="Bash",
                    target=command,
                )
        except Exception:
            # Fallback transparente a las reglas determinísticas originales
            pass

    # 5. Si intenta borrar archivos de código fuera de tmp/
    if has_destructive_cmd and not all_targets_disposable:
        emit_decision(
            "ask",
            f"El comando intenta eliminar archivos permanentes o fuera de tmp/ ({', '.join(destructive_targets)}). "
            "Se requiere confirmación explícita.",
            hook_name="block_dangerous",
            tool_name="Bash",
            target=", ".join(destructive_targets),
        )

    # Registro de auditoría para comando seguro
    record_audit_log("ALLOW", "block_dangerous", "Bash", command, "Comando seguro autorizado")
    sys.exit(0)


def main() -> None:
    data = read_hook_input()
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
