#!/usr/bin/env python3
"""PreToolUse hook — Bloqueo de lectura directa de archivos .env potenciado por Laya System 1.

Reemplaza: /home/bypabloc/projects/bypabloc/optical-soft/.claude/hooks/block-env-read.py

Contexto y Propósito:
  1. Bloquea la LECTURA directa de archivos de credenciales (.env*) tanto vía la herramienta Read
     como vía comandos Bash (cat, head, tail, awk, sed, grep, cut, base64, xxd).
  2. Permite explícitamente el uso legítimo de variables mediante 'source .env' o '. .env',
     cargando las variables en el proceso Bash sin que los secretos pasen al contexto del LLM.
  3. Infiere e imprime las KEYS detectadas junto a su formato sin revelar el valor real
     (e.g., <URL, 24 caracteres>, <booleano>, <numérico, 4 dígitos>, <texto, 12 caracteres>).
  4. Evaluación semántica con Laya System 1 para detectar scripts y comandos de volcado no convencionales.
  5. Fallback automático a verificación sintáctica determinística si no hay GPU o falla Laya.

Vínculos con la investigación en docs/research/laya/:
  - docs/research/laya/08-casos-de-uso-y-patrones-arquitecturales.md:
      Filtrado de datos y prevención de exfiltración de credenciales en agentes autónomos.
  - docs/research/laya/09-recetario-de-ejemplos-practicos.md:
      Técnicas de ofuscación y formateo seguro de metadatos de configuración.
  - docs/research/laya/04-el-router-y-multilingue.md:
      Clasificación de intenciones de comandos shell mediante esquemas tipados.

Ejemplos de Uso en CLI:
  # 1. Caso lectura directa con cat (bloqueado con inferencia de tipos):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "cat .env"}}' | python src/block_env_read.py --gpu
  -> 🚫 BLOCKED: Lectura directa de archivo de credenciales: .env
     Si necesitas USAR las variables (no verlas), usa en Bash:
       set -a; source .env; set +a
     Keys disponibles (formato inferido, valores NUNCA mostrados):
       DATABASE_URL=<URL, 32 caracteres>
       DEBUG=<booleano> [exit 2]

  # 2. Caso lectura con herramienta Read (bloqueado):
  $ echo '{"tool_name": "Read", "tool_input": {"file_path": ".env.production"}}' | python src/block_env_read.py --gpu
  -> 🚫 BLOCKED: Lectura directa de archivo de credenciales [exit 2]

  # 3. Caso carga legítima con source (permitido):
  $ echo '{"tool_name": "Bash", "tool_input": {"command": "set -a; source .env; set +a"}}' | python src/block_env_read.py --gpu
  -> [salida limpia, exit 0]
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

# Agregar directorio actual a sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (
    build_env_block_message,
    emit_block,
    extract_bash_tokens,
    get_laya_router,
    is_env_file,
    read_hook_input,
    record_audit_log,
    resolve_path,
    should_use_laya,
)

READ_COMMANDS = {
    "cat", "head", "tail", "less", "more", "batcat", "bat",
    "awk", "sed", "grep", "egrep", "fgrep", "cut", "xxd", "base64",
    "strings", "hexdump", "od", "nl", "pr", "view", "nano", "vim", "vi",
}

ENV_INTENT_QUESTIONS = {
    "env_intent": {
        "type": "choice",
        "instructions": "Determine if this shell command is attempting to read, display, or exfiltrate contents of an environment (.env) file or credentials, versus loading/sourcing it or running unaffected commands.",
        "criteria": {
            "read_or_exfiltrate": "reading, displaying, dumping, catting, grepping, or extracting secrets from a .env file",
            "source_or_load": "sourcing or loading variables into the shell process without displaying values (e.g. source .env, . .env, set -a)",
            "safe_or_unrelated": "command does not attempt to read or display private environment file contents"
        }
    }
}


def find_env_args_in_bash(command: str, cwd: str) -> list[str]:
    """Detecta si algun sub-comando de lectura clasica toma un .env como argumento de entrada."""
    parts = extract_bash_tokens(command)
    hits: list[str] = []

    for part in parts:
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            continue
        if not tokens:
            continue

        cmd = os.path.basename(tokens[0])
        if cmd not in READ_COMMANDS:
            continue

        # Evitar falsos positivos si el token es destino de redireccion (e.g. cat > .env << 'EOF')
        skip_next = False
        for tok in tokens[1:]:
            if skip_next:
                skip_next = False
                continue
            if tok in {">", ">>", "<<", "<<<", "<"}:
                skip_next = True
                continue
            if tok.startswith(("-", ">", "<")):
                continue

            resolved = resolve_path(tok, cwd)
            if is_env_file(resolved):
                hits.append(resolved)

    return hits


def evaluate_bash_env_read(command: str, cwd: str) -> None:
    """Inspecciona el comando Bash combinando analisis sintactico y clasificacion semantica Laya."""
    if not command.strip():
        sys.exit(0)

    # 1. Chequeo rapido sintactico de comandos de lectura tradicionales
    hits = find_env_args_in_bash(command, cwd)
    if hits:
        record_audit_log("BLOCKED", "block_env_read", "Bash", hits[0], "Lectura directa de archivo .env con comando Bash")
        sys.stderr.write(build_env_block_message(hits[0]) + "\n")
        sys.exit(2)

    # Si el comando no contiene menciones a .env ni variantes, permitir de inmediato
    if not re.search(r"\.env", command, re.IGNORECASE):
        record_audit_log("ALLOW", "block_env_read", "Bash", command, "Comando sin referencia a .env")
        sys.exit(0)

    # Permitir patron legitimo de source explicito
    if re.search(r"(?:^|\s|;)(?:source|\.)\s+[^\s;&|]+\.env", command):
        # Verificar que no contenga pipes hacia comandos de impresion o echo
        if not any(f"| {rc}" in command or f"|{rc}" in command for rc in READ_COMMANDS):
            record_audit_log("ALLOW", "block_env_read", "Bash", command, "Carga legítima mediante source autorizada")
            sys.exit(0)

    # Detección determinística de lectura mediante scripts de una línea (Python, Node, Ruby, Perl)
    scripting_read_pat = r"(?i)(?:python\d*|node|perl|ruby|php)\s+.*(?:\.read\(|readFileSync|open\(|file_get_contents).*\.env"
    if re.search(scripting_read_pat, command):
        record_audit_log("BLOCKED", "block_env_read", "Bash", command, "Lectura programática de .env detectada")
        emit_block(
            f"Intento de lectura directa/programática de archivo .env detectado.\n"
            f"Comando: {command}\n"
            f"Usa 'set -a; source <env-file>; set +a' para cargar variables en el proceso sin exponer valores.",
            exit_code=2,
            hook_name="block_env_read",
            tool_name="Bash",
            target=command,
        )

    # 2. Evaluación semántica con Laya System 1 (si GPU/CUDA está disponible o se solicitó Laya)
    if should_use_laya():
        try:
            router = get_laya_router()
            res = router.predict(command, ENV_INTENT_QUESTIONS)
            answers = res["answers"]

            intent = answers["env_intent"]["choice"]
            conf = answers["env_intent"]["answer_confidence"]

            if intent == "read_or_exfiltrate" and conf >= 0.70:
                match = re.search(r"([^\s;&|'\"]*\.env[^\s;&|'\"]*)", command)
                env_path = match.group(1) if match else ".env"
                resolved_env = resolve_path(env_path, cwd)

                record_audit_log("BLOCKED", "block_env_read", "Bash", command, "Exfiltración semántica de .env detectada por Laya")
                if os.path.exists(resolved_env):
                    sys.stderr.write(build_env_block_message(resolved_env) + "\n")
                else:
                    emit_block(
                        f"Intento semantico de lectura/exfiltracion de variables de entorno detectado.\n"
                        f"Comando: {command}\n"
                        f"Usa 'set -a; source <env-file>; set +a' para cargar variables sin exponer valores.",
                        exit_code=2,
                        hook_name="block_env_read",
                        tool_name="Bash",
                        target=command,
                    )
                sys.exit(2)
        except Exception:
            # Fallback transparente a reglas determinísticas
            pass

    record_audit_log("ALLOW", "block_env_read", "Bash", command, "Comando seguro autorizado")
    sys.exit(0)


def main() -> None:
    data = read_hook_input()
    if not data:
        sys.exit(0)

    tool_name = data.get("tool_name")
    cwd = data.get("cwd") or os.getcwd()

    if tool_name == "Read":
        file_path = data.get("tool_input", {}).get("file_path", "")
        resolved = resolve_path(file_path, cwd)
        if resolved and is_env_file(resolved):
            record_audit_log("BLOCKED", "block_env_read", "Read", resolved, "Lectura directa con herramienta Read")
            sys.stderr.write(build_env_block_message(resolved) + "\n")
            sys.exit(2)
        record_audit_log("ALLOW", "block_env_read", "Read", file_path, "Lectura permitida (no es .env sensible)")
        sys.exit(0)

    if tool_name == "Bash":
        command = data.get("tool_input", {}).get("command", "")
        evaluate_bash_env_read(command, cwd)

    sys.exit(0)


if __name__ == "__main__":
    main()
