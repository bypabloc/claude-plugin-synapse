#!/usr/bin/env python3
"""PreToolUse hook para Edit y Write — Protección de archivos sensibles potenciada por Laya System 1.

Reemplaza: /home/bypabloc/projects/bypabloc/optical-soft/.claude/hooks/protect-files.py

Contexto y Propósito:
  1. Protege la infraestructura e integridad del repositorio contra modificaciones no autorizadas:
     - Bloqueo duro (exit 2): Archivos de entorno (.env*), lockfiles de paquetes (package-lock.json,
       bun.lock, yarn.lock, uv.lock, poetry.lock) y directorios internos (.git/, node_modules/, .venv/).
     - Solicitud de confirmación ('ask', exit 0): Archivos de configuración de agentes y hooks
       (.claude/settings.json, .claude/hooks/**).
  2. Plantillas seguras permitidas: Permite la edición de archivos de ejemplo (.env.example,
     .env.sample, *.template, *.dist) y código fuente estándar de la aplicación.
  3. Evaluación semántica con Laya System 1: Detecta archivos de alta sensibilidad no incluidos
     en patrones explícitos.
  4. Fallback automático a verificación por patrones determinísticos si no hay GPU o falla Laya.

Vínculos con la investigación en docs/research/laya/:
  - docs/research/laya/08-casos-de-uso-y-patrones-arquitecturales.md:
      Protección de archivos base y gobernanza de repositorios en agentes autónomos.
  - docs/research/laya/03-guia-de-uso-y-primitivas.md:
      Clasificación de recursos y rutas con esquemas 'choice'.
  - docs/research/laya/06-despliegue-produccion-y-servidores.md:
      Buenas prácticas en ejecución offline y contención de permisos.

Ejemplos de Uso en CLI:
  # 1. Caso modificación de lockfile (bloqueado duro):
  $ echo '{"tool_name": "Edit", "tool_input": {"file_path": "package-lock.json"}}' | python src/protect_files.py --gpu
  -> 🚫 BLOCKED: Cannot modify protected file or directory: package-lock.json [exit 2]

  # 2. Caso modificación de hooks/settings (solicita confirmación):
  $ echo '{"tool_name": "Edit", "tool_input": {"file_path": ".claude/settings.json"}}' | python src/protect_files.py --gpu
  -> {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", ...}} [exit 0]

  # 3. Caso plantilla de entorno (permitido):
  $ echo '{"tool_name": "Write", "tool_input": {"file_path": ".env.example"}}' | python src/protect_files.py --gpu
  -> [salida limpia, exit 0]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Agregar directorio actual a sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (
    ASK_PATTERNS,
    PROTECTED_PATTERNS,
    TEMPLATE_SUFFIXES,
    emit_block,
    emit_decision,
    get_laya_router,
    read_hook_input,
    record_audit_log,
    resolve_path,
    should_use_laya,
)

FILE_SENSITIVITY_QUESTIONS = {
    "file_sensitivity": {
        "type": "choice",
        "instructions": "Evaluate the sensitivity of modifying or overwriting this target file path.",
        "criteria": {
            "critical_blocked": "critical lockfile, internal git repository structure, virtual environment, or raw environment secrets file",
            "ask_confirmation": "agent configuration file, security hooks, or IDE configuration requiring user confirmation",
            "template_allowed": "example configuration template, sample env file, or documentation mockup",
            "standard_source": "application source code, unit test, documentation, or temporary file"
        }
    }
}


def evaluate_target_path(file_path: str, cwd: str, tool_name: str = "Edit/Write") -> None:
    """Evalúa la ruta objetivo combinando verificaciones determinísticas y Laya System 1."""
    if not file_path:
        sys.exit(0)

    norm_path = resolve_path(file_path, cwd)
    p = Path(norm_path)
    file_name = p.name

    # 1. Comprobar si es un directorio protegido
    in_protected_dir = any(part in {".git", "node_modules", ".venv"} for part in p.parts)

    # 2. Las plantillas (.env.example) se permiten SIEMPRE que no estén dentro de un directorio protegido
    is_editable_template = not in_protected_dir and file_name.endswith(TEMPLATE_SUFFIXES)
    if is_editable_template:
        record_audit_log("ALLOW", "protect_files", tool_name, file_path, "Plantilla de configuración editable autorizada")
        sys.exit(0)

    # 3. Bloqueo determinístico para patrones protegidos críticos
    if in_protected_dir or any(pat in norm_path or pat == file_name for pat in PROTECTED_PATTERNS):
        emit_block(
            f"Cannot modify protected file or directory: {file_path}",
            exit_code=2,
            hook_name="protect_files",
            tool_name=tool_name,
            target=file_path,
        )

    # 4. Solicitud de confirmación determinística para configuraciones de Claude
    if any(pat in norm_path for pat in ASK_PATTERNS):
        emit_decision(
            "ask",
            f"Sensitive configuration requires explicit approval: {file_path}",
            hook_name="protect_files",
            tool_name=tool_name,
            target=file_path,
        )

    # 5. Evaluación semántica con Laya System 1 (si GPU/CUDA está disponible o se solicitó Laya)
    if should_use_laya():
        try:
            router = get_laya_router()
            res = router.predict(file_path, FILE_SENSITIVITY_QUESTIONS)
            answers = res["answers"]

            sensitivity = answers["file_sensitivity"]["choice"]
            confidence = answers["file_sensitivity"]["answer_confidence"]

            if sensitivity == "critical_blocked" and confidence >= 0.70:
                emit_block(
                    f"Target file classified as critical/protected by Laya System 1: {file_path}",
                    exit_code=2,
                    hook_name="protect_files",
                    tool_name=tool_name,
                    target=file_path,
                )

            if sensitivity == "ask_confirmation" and confidence >= 0.70:
                emit_decision(
                    "ask",
                    f"Target file classified as sensitive config by Laya System 1: {file_path}",
                    hook_name="protect_files",
                    tool_name=tool_name,
                    target=file_path,
                )
        except Exception:
            # Fallback transparente a reglas determinísticas de protección
            pass

    record_audit_log("ALLOW", "protect_files", tool_name, file_path, "Modificación de archivo estándar autorizada")
    sys.exit(0)


def main() -> None:
    data = read_hook_input()
    if not data:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in {"Edit", "Write"}:
        sys.exit(0)

    file_path = data.get("tool_input", {}).get("file_path", "")
    cwd = data.get("cwd") or os.getcwd()

    evaluate_target_path(file_path, cwd, tool_name)


if __name__ == "__main__":
    main()
